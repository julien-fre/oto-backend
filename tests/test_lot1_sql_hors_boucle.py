"""Premier lot de décharge de la boucle : les routes publiques et les deux handlers les plus lourds.

Le serveur est mono-loop (`docs/event-loop-perf.md`) : un `async def` qui lit la base
en synchrone tient TOUT le processus le temps de la lecture (gel de prod du 21/09/2026).
Ces bancs OBSERVENT la boucle plutôt que le source : pendant une lecture qui dort 0,5 s,
une tâche incrémente un compteur toutes les 10 ms — boucle tenue, le compteur n'avance pas.

Le lot couvre d'abord ce qui est atteignable SANS jeton (un tiers peut le marteler) :
les pages de partage, les désinscriptions, les vitrines, le catalogue anonyme,
l'aperçu d'invitation, le dispatch par Host, le contrôle TLS de Caddy, la métadonnée
de ressource protégée et le retour OAuth Salesforce ; puis `runner.triggers` (une
dizaine d'appels base par opération) et `me.credential.set`.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from starlette.requests import Request

_LECTURE_S = 0.5
_BATTEMENTS_MIN = 20          # 0,5 s / 10 ms = 50 attendus, boucle libre ; on exige 20


async def _battements_pendant(coro):
    ticks = 0

    async def _battement():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    tache = asyncio.create_task(_battement())
    await asyncio.sleep(0)
    try:
        rep = await coro
    finally:
        tache.cancel()
    return rep, ticks


def _lente(valeur=None):
    def _lecture(*_a, **_k):
        time.sleep(_LECTURE_S)               # le SQL synchrone
        return valeur
    return _lecture


def _requete(path="/x", headers=None, path_params=None, query=b""):
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": headers or [], "query_string": query,
                    "path_params": path_params or {}})


def _verdict(ticks):
    return (f"la boucle n'a battu que {ticks} fois pendant une lecture de "
            f"{_LECTURE_S} s (≥ {_BATTEMENTS_MIN} attendus) : du SQL synchrone tourne dans "
            "la boucle — le serveur est mono-loop, tout gèle (docs/event-loop-perf.md)")


# ── les routes publiques (sans jeton) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_public_doc_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp.api import public
    monkeypatch.setattr(public.db, "get_doc_by_public_token",
                        _lente({"title": "t", "body_md": "b"}))
    rep, ticks = await _battements_pendant(
        public.public_doc(_requete(path_params={"token": "abc"})))
    assert rep.status_code == 200, "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


@pytest.mark.asyncio
async def test_desinscription_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp import outreach_optout
    from oto_mcp.api import public
    from oto_mcp.db import outreach as db_outreach
    monkeypatch.setattr(outreach_optout, "verify", lambda token: "sub-x")
    monkeypatch.setattr(db_outreach, "desinscrire", _lente())
    monkeypatch.setattr(public.db, "get_user", lambda sub: {"locale": "fr"})
    rep, ticks = await _battements_pendant(
        public.outreach_unsubscribe(_requete(path_params={"token": "t"})))
    assert rep.status_code == 200, "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


@pytest.mark.asyncio
async def test_catalogue_anonyme_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp.api import public
    monkeypatch.setattr(public.connector_activation, "exposed_connectors",
                        _lente({"serper"}))
    rep, ticks = await _battements_pendant(
        public.connectors_catalog(_requete(), verifier=None))
    assert rep.status_code == 200, "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


def test_en_thread_garde_l_identite_de_la_route_et_sort_de_la_boucle():
    """Le décorateur ne doit rien changer pour ceux qui tiennent la route : ni le nom (la
    table des routes), ni l'objet (`route.endpoint is …`), ni le caractère `async`."""
    import inspect

    from oto_mcp.api import public
    from oto_mcp.api.base import en_thread

    vu = {}

    @en_thread
    def _route(request):
        """doc de la route"""
        vu["thread"] = threading.current_thread()
        return "ok"

    assert inspect.iscoroutinefunction(_route)
    assert _route.__name__ == "_route" and _route.__doc__ == "doc de la route"
    assert asyncio.run(_route(None)) == "ok"
    assert vu["thread"] is not threading.main_thread(), "le corps a tourné dans la boucle"
    assert inspect.iscoroutinefunction(public.public_doc_view)


# ── le dispatch par Host, le contrôle TLS, le catalogue de projets ────────────────────

@pytest.mark.asyncio
async def test_le_dispatch_par_host_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp import subdomain_project as sp
    monkeypatch.setattr(sp.config, "project_domain", lambda: "oto.cx", raising=False)
    monkeypatch.setattr("oto_mcp.db.get_project_by_mcp_slug", _lente(None))
    rep, ticks = await _battements_pendant(sp.resolve_project_async("ft.mcp.oto.cx"))
    assert rep is None
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


@pytest.mark.asyncio
async def test_un_host_canonique_ne_paie_pas_le_saut_de_thread(monkeypatch):
    """Le cas le plus courant (aucun slug) ne lit rien : il ne doit ni toucher la base ni
    quitter la boucle."""
    from oto_mcp import subdomain_project as sp
    monkeypatch.setattr("oto_mcp.db.get_project_by_mcp_slug",
                        lambda slug: pytest.fail("aucune lecture attendue pour un host sans slug"))
    assert await sp.resolve_project_async("mcp.oto.cx") is None


@pytest.mark.asyncio
async def test_le_slug_d_org_inconnu_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp import org_store, subdomain_org
    monkeypatch.setattr(subdomain_org, "_CACHE", {})
    monkeypatch.setattr(subdomain_org.config, "public_host", lambda: "mcp.oto.cx")
    monkeypatch.setattr(org_store, "list_all_orgs", _lente([]))
    rep, ticks = await _battements_pendant(
        subdomain_org.org_id_for_host_async("inconnu--mcp.oto.cx"))
    assert rep is None
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


# ── les deux handlers les plus lourds ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runner_triggers_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp.capabilities import runner_triggers as rt
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(rt.db, "list_triggers", _lente([]))
    monkeypatch.setattr(rt.db, "runner_arme", lambda org: {"armed": True, "workers": 1,
                                                          "last_seen": None})
    out, ticks = await _battements_pendant(
        rt._triggers(ResolvedCtx(sub="u", org_id=7), rt.TriggerInput(op="list")))
    assert out["triggers"] == [], "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)


@pytest.mark.asyncio
async def test_me_credential_set_ne_gele_pas_la_boucle(monkeypatch):
    from oto_mcp import credentials_store
    from oto_mcp.capabilities import me_credentials as mc
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.connectors import verify as connector_verify
    ecrit = []
    monkeypatch.setattr(mc.access, "current_org", lambda sub: 35)
    monkeypatch.setattr(mc.db, "upsert_user", _lente())
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **k: ecrit.append(k) or time.sleep(_LECTURE_S))
    monkeypatch.setattr(credentials_store, "guard_account_write", lambda *a, **k: None)
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", lambda *a, **k: None)
    monkeypatch.setattr(connector_verify, "supports", lambda p: False)
    out, ticks = await _battements_pendant(
        mc._set(ResolvedCtx(sub="u", org_id=35),
                mc.CredentialSetInput(provider="serper", fields={"key": "K"})))
    assert out["ok"] and ecrit, "l'écriture n'a pas été jouée : la garde serait inerte"
    # deux temps lents (la lecture, puis l'écriture) : ~1 s de SQL au total, donc ~100
    # battements boucle libre ; on en exige le double du seuil d'un seul temps
    assert ticks >= 2 * _BATTEMENTS_MIN, _verdict(ticks)


@pytest.mark.asyncio
async def test_oto_connector_ne_gele_pas_la_boucle(monkeypatch):
    """Trouvé par la garde d'EXÉCUTION en CI (pas par le balayage : l'alias local
    `sel = connectors_selection` cachait les appels) — `recommend` écrit le kit d'une org."""
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.connectors import console, selection
    monkeypatch.setattr(selection, "_me", _lente({"connectors": []}))
    out, ticks = await _battements_pendant(
        console._connector(ResolvedCtx(sub="u", org_id=7), console.ConnectorInput(op="list")))
    assert out == {"connectors": []}, "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= _BATTEMENTS_MIN, _verdict(ticks)
