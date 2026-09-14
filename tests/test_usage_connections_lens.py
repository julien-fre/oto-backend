"""`org.usage.connections` — les connexions de messagerie d'une org, lentille MEMBRE.

Une lecture NEUTRE : les comptes de messagerie hébergés d'UNE org et leurs dates,
toutes clés confondues, `platform_seat` disant laquelle. Aucune règle de facturation
ici — ce qu'un relevé en compte est l'affaire du consommateur :

1. **Aucune identité** : ni `sub`, ni `email`, ni `account_id` Unipile. Une clé opaque,
   stable d'une reconnexion à l'autre, suffit à dédupliquer.
2. **Aucun tri** : une connexion sur une clé propre (BYO) est rendue, `platform_seat`
   à False.
3. **Bornable** : `since` rend aussi les connexions coupées depuis — coupée le 12, une
   connexion a existé ce mois-là.
4. **Lisible par un membre, invisible des agents** : ce n'est pas un outil MCP.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from oto_mcp.capabilities import org_monitoring as om
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES
from oto_mcp.db import unipile as db_unipile

CTX = ResolvedCtx(sub="membre", org_id=7)


def _fake(monkeypatch, rows):
    vu: dict = {}

    def faux(org_id, since=None):
        vu.update(org_id=org_id, since=since)
        return [dict(r) for r in rows]

    monkeypatch.setattr(om.db, "list_org_unipile_connections", faux)
    return vu


def _row(key="k1", provider="LINKEDIN", seat=True, **extra):
    return {"connection_key": key, "provider": provider, "platform_seat": seat,
            "connected_at": "2026-09-01T10:00:00Z", "disconnected_at": None, **extra}


def test_la_lentille_ne_rend_aucune_identite(monkeypatch):
    # ce que le store pourrait laisser fuiter, et que la projection doit ignorer
    _fake(monkeypatch, [_row(sub="acme:abc", email="x@y.z", account_id="acc_1")])
    out = om._connections(CTX, om.OrgConnectionsInput(org_id=7))
    assert out == {"connections": [{"connection_key": "k1", "provider": "LINKEDIN",
                                    "platform_seat": True,
                                    "connected_at": "2026-09-01T10:00:00Z",
                                    "disconnected_at": None}]}


def test_une_connexion_BYO_est_rendue_la_lentille_ne_trie_pas(monkeypatch):
    _fake(monkeypatch, [_row("k1", seat=True), _row("k2", "WHATSAPP", seat=False)])
    out = om._connections(CTX, om.OrgConnectionsInput(org_id=7))
    assert [(c["connection_key"], c["platform_seat"]) for c in out["connections"]] == [
        ("k1", True), ("k2", False)]


def test_la_requete_ne_filtre_pas_sur_la_cle(monkeypatch):
    vu: dict = {}

    class _Res:
        def fetchall(self):
            return [{"sub": "acme:a", "provider": "LINKEDIN", "platform_seat": False,
                     "connected_at": "2026-09-01T10:00:00Z", "disconnected_at": None}]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            vu.update(sql=sql, params=params)
            return _Res()

    monkeypatch.setattr(db_unipile, "_connect", lambda: _Conn())
    [row] = db_unipile.list_org_unipile_connections(7)
    where = vu["sql"].split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "platform_seat" not in where
    assert row["platform_seat"] is False and "sub" not in row


def test_since_est_transmis_tel_quel(monkeypatch):
    vu = _fake(monkeypatch, [])
    om._connections(CTX, om.OrgConnectionsInput(org_id=7, since="2026-10-01T00:00:00+00:00"))
    assert vu == {"org_id": 7, "since": "2026-10-01T00:00:00+00:00"}


def test_sans_since_la_lecture_n_est_pas_bornee(monkeypatch):
    vu = _fake(monkeypatch, [])
    om._connections(CTX, om.OrgConnectionsInput(org_id=7))
    assert vu == {"org_id": 7, "since": None}


def test_un_since_qui_n_est_pas_une_date_est_refuse_avant_la_base():
    with pytest.raises(ValidationError):
        om.OrgConnectionsInput(org_id=7, since="hier")


def test_la_cle_est_stable_par_connexion_et_ne_nomme_pas_le_membre():
    k = db_unipile.unipile_connection_key(7, "acme:abc", "LINKEDIN")
    assert k == db_unipile.unipile_connection_key(7, "acme:abc", "LINKEDIN")
    assert k != db_unipile.unipile_connection_key(8, "acme:abc", "LINKEDIN")
    assert k != db_unipile.unipile_connection_key(7, "acme:abc", "WHATSAPP")
    assert k != db_unipile.unipile_connection_key(7, "acme:xyz", "LINKEDIN")
    assert len(k) == 24 and "acme" not in k


def test_capacite_membre_en_rest_jamais_en_mcp():
    cap = next(c for c in CAPABILITIES if c.key == "org.usage.connections")
    assert cap.mcp is None
    assert cap.authz is om._MEMBER_OF
    assert (cap.rest.verb, cap.rest.path) == ("GET", "/api/orgs/{id}/usage/connections")
