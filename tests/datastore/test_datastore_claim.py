"""Réserver une ligne depuis une application web (signal #362).

`claim_next` et le bail existaient côté MCP seulement : un front pouvait lire la
file et libérer, jamais RÉSERVER — d'où des verrous réécrits dans les données de la
ligne, non atomiques. Ces tests figent les trois gestes et leurs gardes : réserver
la suivante, réserver une ligne nommée, et ne pas libérer le bail d'un autre.

Les deux claims sont des CAPACITÉS (une surface neuve naît capacité) → handlers
appelés en direct. La LIBÉRATION l'est devenue à son tour le 2026-08-12 (#302) :
elle s'exerce ici par sa route, sur la vraie chaîne de l'adaptateur REST — c'est là
que vivent son corps optionnel et son refus de jeton porté. Seams PG monkeypatchés
(le chemin SQL est vérifié au deploy).
"""
from __future__ import annotations

import pytest

from _datastore_rest import call, stub_authz

from oto_mcp.datastore import core as D
from oto_mcp.auth import token_scopes
from oto_mcp.capabilities.datastore import claim as dsc
from oto_mcp.capabilities.datastore import rows as dsr
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.datastore.core import NamespaceNotFound, RowClaimed, RowNotFound

# ── Le store : réserver une ligne NOMMÉE ─────────────────────────────────────

def _store(monkeypatch, ns_id=7):
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: ns_id)
    monkeypatch.setattr(s, "_ns_of", lambda _id: {"namespace": "vivier", "schema": None})
    return s


def test_claim_row_returns_the_row_with_its_lease(monkeypatch):
    seen = {}

    def _claim(ns_id, row_id, *, worker, lease_seconds, **k):
        seen.update(ns_id=ns_id, row_id=row_id, worker=worker, lease=lease_seconds)
        return {"row_id": "r1", "created_at": "c", "updated_at": "u",
                "data": {"nom": "ACME"}, "claimed_by": "sarah", "claimed_until": "t+15"}

    monkeypatch.setattr(D.db, "datastore_claim_row", _claim)
    out = _store(monkeypatch).claim_row("vivier", "r1", worker="sarah", lease_s=300)
    assert out == {"_id": "r1", "_created_at": "c", "_updated_at": "u", "nom": "ACME",
                   "_claimed_by": "sarah", "_claimed_until": "t+15"}
    assert (seen["ns_id"], seen["row_id"], seen["worker"], seen["lease"]) == (7, "r1", "sarah", 300)


def test_claim_row_distinguishes_absent_from_taken(monkeypatch):
    """Un `None` commun ne dit pas à l'utilisateur ce qui s'est passé : ligne
    disparue ou collègue plus rapide sont deux réponses différentes."""
    monkeypatch.setattr(D.db, "datastore_claim_row", lambda *a, **k: None)
    monkeypatch.setattr(D.db, "datastore_get_row", lambda ns_id, row_id: None)
    with pytest.raises(RowNotFound):
        _store(monkeypatch).claim_row("vivier", "r1", worker="sarah")

    monkeypatch.setattr(D.db, "datastore_get_row", lambda ns_id, row_id: {
        "row_id": "r1", "claimed_by": "jules", "claimed_until": "t+9"})
    with pytest.raises(RowClaimed) as e:
        _store(monkeypatch).claim_row("vivier", "r1", worker="sarah")
    assert (e.value.claimed_by, e.value.claimed_until) == ("jules", "t+9")


def test_claim_row_requires_a_worker(monkeypatch):
    """Sans libellé de porteur, le bail n'a plus de garde au release."""
    monkeypatch.setattr(D.db, "datastore_claim_row", lambda *a, **k: {})
    with pytest.raises(ValueError):
        _store(monkeypatch).claim_row("vivier", "r1", worker="   ")


def test_claim_row_needs_write_access(monkeypatch):
    seen = {}

    def _resolve(ns, write=False):
        seen["write"] = write
        return 7

    monkeypatch.setattr(D.db, "datastore_claim_row", lambda *a, **k: {
        "row_id": "r1", "created_at": "c", "updated_at": "u", "data": {},
        "claimed_by": "sarah", "claimed_until": "t"})
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", _resolve)
    monkeypatch.setattr(s, "_ns_of", lambda _id: {"namespace": "vivier", "schema": None})
    s.claim_row("vivier", "r1", worker="sarah")
    assert seen["write"] is True    # poser un bail = écrire, un partage en lecture ne réserve pas


def test_claims_report_the_configuration_that_breaks_auto_release(monkeypatch):
    """Le warning de `data_claim_next` vaut pour les DEUX claims : la ligne d'un
    tableau sans état terminal ne se libère jamais toute seule."""
    monkeypatch.setattr(D.db, "datastore_claim_row", lambda *a, **k: {
        "row_id": "r1", "created_at": "c", "updated_at": "u", "data": {},
        "claimed_by": "sarah", "claimed_until": "t"})
    monkeypatch.setattr(D.dsv2, "queue_release_warning", lambda schema: "pas d'état terminal")
    warnings: list = []
    _store(monkeypatch).claim_row("vivier", "r1", worker="sarah", warnings=warnings)
    assert warnings == ["pas d'état terminal"]


# ── Les capacités : réserver depuis un front ─────────────────────────────────

class _Store:
    """Store de test : enregistre les appels, rejoue les réponses programmées."""

    def __init__(self, **outcomes):
        self.outcomes = outcomes
        self.calls: list = []

    def _play(self, name, kwargs):
        self.calls.append((name, kwargs))
        out = self.outcomes.get(name)
        if isinstance(out, Exception):
            raise out
        return out

    def claim_next(self, namespace, **k):
        return self._play("claim_next", {"namespace": namespace, **k})

    def claim_row(self, namespace, row_id, **k):
        return self._play("claim_row", {"namespace": namespace, "row_id": row_id, **k})

    def release_claim(self, namespace, row_id, **k):
        return self._play("release_claim", {"namespace": namespace, "row_id": row_id, **k})

    def force_release(self, namespace, row_id, **k):
        return self._play("force_release", {"namespace": namespace, "row_id": row_id, **k})


def _cap(monkeypatch, store, handler, Input, **fields):
    monkeypatch.setattr(dsc, "make_store", lambda sub: store)
    monkeypatch.setattr(dsc.datastore_journal, "record", lambda *a, **k: None)
    return handler(ResolvedCtx(sub="u-1"), Input(**fields))


def _claim_next(monkeypatch, store, **fields):
    return _cap(monkeypatch, store, dsc._claim_next, dsc.ClaimNextInput, **fields)


def _claim_row(monkeypatch, store, **fields):
    return _cap(monkeypatch, store, dsc._claim_row, dsc.ClaimRowInput, **fields)


ROW = {"_id": "r1", "nom": "ACME", "_claimed_by": "sarah", "_claimed_until": "t+15"}


def test_claim_next_reserves_and_returns_the_row(monkeypatch):
    store = _Store(claim_next=ROW)
    out = _claim_next(monkeypatch, store, namespace="vivier", worker="sarah",
                      filter={"statut": "a-appeler"}, lease_s=300)
    assert out == {"namespace": "vivier", "row": ROW}
    _, kw = store.calls[0]
    assert (kw["worker"], kw["filter"], kw["lease_s"]) == ("sarah", {"statut": "a-appeler"}, 300)


def test_claim_next_on_an_empty_queue_says_so(monkeypatch):
    out = _claim_next(monkeypatch, _Store(claim_next=None), namespace="vivier", worker="sarah")
    assert out["row"] is None
    assert "hint" in out           # file vide ≠ erreur, mais ça se dit


def test_claim_lease_is_optional_and_defaults_server_side(monkeypatch):
    """Ne pas relayer un `lease_s` absent : le défaut appartient au store, pas au front."""
    store = _Store(claim_next=ROW)
    _claim_next(monkeypatch, store, namespace="vivier", worker="sarah")
    assert "lease_s" not in store.calls[0][1]


def test_claim_next_reports_the_configuration_warning(monkeypatch):
    store = _Store(claim_next=ROW)

    def _claim(namespace, **k):
        k["warnings"].append("statut sans état terminal")
        return ROW

    monkeypatch.setattr(store, "claim_next", _claim)
    assert _claim_next(monkeypatch, store, namespace="vivier",
                       worker="sarah")["warning"] == "statut sans état terminal"


@pytest.mark.parametrize("worker", ["", "   "])
def test_claim_without_worker_is_refused(monkeypatch, worker):
    """`worker` EST la garde du bail — sans lui, la file redevient coopérative."""
    store = _Store()
    for handler in (
        lambda: _claim_next(monkeypatch, store, namespace="vivier", worker=worker),
        lambda: _claim_row(monkeypatch, store, namespace="vivier", row_id="r1", worker=worker),
    ):
        with pytest.raises(AuthzDenied) as e:
            handler()
        assert (e.value.status, e.value.code) == (400, "worker_required")
        assert "release" in e.value.message      # un refus qui dit à quoi sert le champ
    assert store.calls == []                     # rien n'a été réservé


def test_claim_row_conflict_names_who_holds_the_lease(monkeypatch):
    store = _Store(claim_row=RowClaimed("r1", "jules", "t+9"))
    with pytest.raises(AuthzDenied) as e:
        _claim_row(monkeypatch, store, namespace="vivier", row_id="r1", worker="sarah")
    assert (e.value.status, e.value.code) == (409, "row_claimed")
    assert "jules" in e.value.message


def test_claim_row_404_when_the_row_is_gone(monkeypatch):
    with pytest.raises(AuthzDenied) as e:
        _claim_row(monkeypatch, _Store(claim_row=RowNotFound("r1")),
                   namespace="vivier", row_id="r1", worker="sarah")
    assert (e.value.status, e.value.code) == (404, "row_not_found")


def test_claim_on_an_unknown_namespace_is_a_404(monkeypatch):
    """Un tableau hors périmètre ne se distingue pas d'un tableau inexistant."""
    with pytest.raises(AuthzDenied) as e:
        _claim_next(monkeypatch, _Store(claim_next=NamespaceNotFound("nope")),
                    namespace="inconnu", worker="sarah")
    assert (e.value.status, e.value.code) == (404, "namespace_not_found")


def test_claim_on_a_read_only_share_is_refused(monkeypatch):
    """Poser un bail est une écriture : un tableau partagé en lecture ne se réserve pas."""
    with pytest.raises(AuthzDenied) as e:
        _claim_next(monkeypatch, _Store(claim_next=D.NamespaceReadOnly("vivier")),
                    namespace="vivier", worker="sarah")
    assert (e.value.status, e.value.code) == (403, "namespace_read_only")


def test_the_claims_are_rest_only_capabilities():
    """Opt-out MCP explicite : `data_claim_next` tient déjà la face agent."""
    caps = {c.key: c for c in dsc.CAPABILITIES if c.key.startswith("me.datastore.claim")}
    assert set(caps) == {"me.datastore.claim_next", "me.datastore.claim_row"}
    for cap in caps.values():
        assert cap.mcp is None
        assert [b.verb for b in cap.rest_bindings()] == ["POST"]
        assert cap.Output is not None     # la forme de la réponse est déclarée


# ── Release : deux régimes, selon ce que l'appelant sait ──────────────────────
# Capacité depuis #302 (`me.datastore.release_claim`), même chemin qu'avant.

def _call(monkeypatch, store, body, *, no_body=False):
    stub_authz(monkeypatch)
    monkeypatch.setattr(dsr, "make_store", lambda sub: store)
    monkeypatch.setattr(dsr.datastore_journal, "record", lambda *a, **k: None)
    return call("me.datastore.release_claim",
                path_params={"namespace": "vivier", "row_id": "r1"},
                body=body, no_body=no_body)


def test_release_with_a_worker_is_guarded(monkeypatch):
    store = _Store(release_claim=True)
    status, _ = _call(monkeypatch, store, {"worker": "sarah"})
    assert status == 200
    name, kw = store.calls[0]
    assert (name, kw["worker"]) == ("release_claim", "sarah")   # pas force_release


def test_guarded_release_of_someone_elses_lease_changes_nothing(monkeypatch):
    store = _Store(release_claim=False)
    status, payload = _call(monkeypatch, store, {"worker": "sarah"})
    assert status == 200
    assert payload["released"] is False
    assert "autre worker" in payload["hint"]


def test_release_without_body_is_the_supervision_gesture(monkeypatch):
    """Le dashboard (session interactive) garde la libération forcée d'un bail bloqué.
    Il poste SANS corps : la capacité doit s'en accommoder, pas exiger `{}`."""
    store = _Store(force_release=True)
    token_scopes.set_current(None)
    status, _ = _call(monkeypatch, store, None, no_body=True)
    assert status == 200
    assert store.calls[0][0] == "force_release"


def test_a_scoped_token_cannot_force_release(monkeypatch):
    """Un jeton porté = une intégration multi-utilisateurs : y laisser la
    libération forcée, c'est laisser chacun retirer la ligne de son collègue."""
    store = _Store(force_release=True)
    token_scopes.set_current({"namespaces": {"vivier": "write"}})
    try:
        status, corps = _call(monkeypatch, store, {})
    finally:
        token_scopes.set_current(None)
    assert (status, corps["error"]) == (400, "worker_required")
    assert store.calls == []


def test_a_scoped_token_releases_its_own_lease(monkeypatch):
    store = _Store(release_claim=True)
    token_scopes.set_current({"namespaces": {"vivier": "write"}})
    try:
        status, _ = _call(monkeypatch, store, {"worker": "sarah"})
    finally:
        token_scopes.set_current(None)
    assert status == 200
    assert store.calls[0][0] == "release_claim"
