"""QUI A TROUVÉ chaque valeur : `outil` et `fournisseur` sur le journal des révisions,
`data_write(via=…)`, et l'agrégat par colonne servi à l'en-tête
(`GET …/column-sources`).

Tout se juge sur ce que porte la BASE, après être passé par le vrai chemin :

1. **l'outil de l'appel** — un appel MCP estampille son outil (`oto_call` déplié) ;
2. **le fournisseur** — `geste.par_fournisseur` le pose sur la seule écriture qu'il
   entoure ;
3. **`via`** — accepté seulement si l'outil a RÉUSSI dans le run de l'appel ; refusé
   sans run, hors run, ou sur un outil de la plateforme ;
4. **l'agrégat** — la valeur ACTUELLE de chaque cellule tenue, comptée par ce qui l'a
   écrite ; une ligne supprimée et une valeur effacée ne comptent pas ;
5. **la face REST** — la forme servie, et le 404 d'un tableau hors périmètre.
"""
from __future__ import annotations

import uuid

import pytest

SUB = "sub-sources-colonnes"
AUTRE = "sub-sources-colonnes-autre"


def _sql(requete: str, *params):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(requete, params or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    for s in (SUB, AUTRE):
        db.upsert_user(s, email=f"{s}@sources.invalid", name=s)
    return SUB


def _table(proprietaire: str = SUB) -> tuple[str, int]:
    from oto_mcp import db
    ns = f"sources-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("user", proprietaire, ns)
    db.set_datastore_schema(ns_id, {"fields": [
        {"key": "nom", "type": "text"}, {"key": "email", "type": "text"}]})
    return ns, ns_id


def _revisions(ns_id: int) -> list[dict]:
    return _sql("SELECT row_id, source, outil, fournisseur, diff "
                "FROM datastore_row_revisions WHERE ns_id = %s ORDER BY id", ns_id)


def _maj(ns_id: int, row_id: str, patch: dict) -> None:
    from oto_mcp import db
    db.datastore_merge_row_locked(ns_id, row_id, lambda d: {**d, **patch},
                                  "2026-11-11T10:00:00")


def _run() -> str:
    from oto_mcp import db
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=SUB, org_id=None, label="t-sources")
    return run_id


def _appel_journalise(run_id: str, tool: str, *, ok: bool = True, args=None) -> None:
    import json
    _sql("INSERT INTO tool_calls (kind, sub, tool, args, ok, run_id) "
         "VALUES ('mcp', %s, %s, %s::jsonb, %s, %s)",
         SUB, tool, json.dumps(args or {}), ok, run_id)


# ── 1. l'outil de l'appel ──────────────────────────────────────────────────────

def test_oto_call_est_deplie_vers_sa_cible():
    from oto_mcp.calllog import _outil_ecrivant
    assert _outil_ecrivant("jev_rows", {}) == "jev_rows"
    assert _outil_ecrivant("oto_call", {"name": "waterfall_email_act",
                                        "arguments": {}}) == "waterfall_email_act"
    assert _outil_ecrivant("oto_call", {"name": "  "}) == "oto_call"
    assert _outil_ecrivant("oto_call", None) == "oto_call"


@pytest.fixture
def mcp(compte, monkeypatch):
    """`data_write` derrière la chaîne servie : contexte d'appel, puis journal."""
    from fastmcp import FastMCP

    from oto_mcp import call_axes
    from oto_mcp.calllog import ToolCallLogger
    from oto_mcp.datastore.core import make_store
    from oto_mcp.middleware.call_context import CallContextMiddleware
    from oto_mcp.tools import datastore as T
    from oto_mcp.tools import register_all

    monkeypatch.setattr(T, "_acting_store", lambda: make_store(SUB))
    monkeypatch.setattr(T, "_ns", lambda ns: ns)
    monkeypatch.setattr(T, "_project_hint", lambda ns: None)
    monkeypatch.setattr(call_axes, "current_user_sub_from_token", lambda: SUB)

    async def sink(row):
        return None

    async def identite():
        return {"sub": SUB}

    m = FastMCP("t-sources")
    register_all(m)
    m.add_middleware(CallContextMiddleware(frozenset()))
    m.add_middleware(ToolCallLogger(sink, server="t", identity=identite))
    return m


async def _ecrire(m, arguments: dict, *, refus: bool = False):
    import asyncio

    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from oto_mcp import calllog
    async with Client(m) as c:
        if refus:
            with pytest.raises(ToolError) as e:
                await c.call_tool("data_write", arguments)
            return str(e.value)
        await c.call_tool("data_write", arguments)
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)
    return None


@pytest.mark.asyncio
async def test_un_appel_mcp_estampille_son_outil(mcp):
    ns, ns_id = _table()
    await _ecrire(mcp, {"datastore": ns, "row": {"nom": "Ada"}})
    (r,) = _revisions(ns_id)
    assert (r["source"], r["outil"], r["fournisseur"]) == ("agent", "data_write", None)


# ── 2. le fournisseur ──────────────────────────────────────────────────────────

def test_le_fournisseur_ne_vaut_que_pour_l_ecriture_qu_il_entoure(compte):
    from oto_mcp import db, geste
    _, ns_id = _table()
    with geste.portee(geste.AGENT, SUB, outil="waterfall_email_act"):
        with geste.par_fournisseur("Hunter"):
            db.datastore_insert_row(ns_id, "r1", {"email": "a@x.fr"})
        db.datastore_insert_row(ns_id, "r2", {"email": "b@x.fr"})
    a, b = _revisions(ns_id)
    assert (a["outil"], a["fournisseur"]) == ("waterfall_email_act", "hunter")
    assert (b["outil"], b["fournisseur"]) == ("waterfall_email_act", None)


def test_sans_geste_ni_l_outil_ni_le_fournisseur_ne_s_inventent(compte):
    from oto_mcp import db
    _, ns_id = _table()
    db.datastore_insert_row(ns_id, "r1", {"nom": "x"})
    (r,) = _revisions(ns_id)
    assert (r["source"], r["outil"], r["fournisseur"]) == ("system", None, None)


# ── 3. `via` ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_via_prouve_dans_le_run_nomme_l_outil(mcp):
    ns, ns_id = _table()
    run = _run()
    _appel_journalise(run, "apollo_match_person")
    _appel_journalise(run, "oto_call", args={"name": "hunter_find_email"})
    await _ecrire(mcp, {"datastore": ns, "row": {"email": "a@x.fr"},
                        "via": "apollo_match_person", "_run_id": run})
    await _ecrire(mcp, {"datastore": ns, "row": {"email": "b@x.fr"},
                        "via": "hunter_find_email", "_run_id": run})
    assert [(r["source"], r["outil"]) for r in _revisions(ns_id)] == [
        ("agent", "apollo_match_person"), ("agent", "hunter_find_email")]


@pytest.mark.asyncio
async def test_via_refuse_sans_preuve(mcp):
    ns, ns_id = _table()
    run = _run()
    _appel_journalise(run, "apollo_match_person", ok=False)
    msg = await _ecrire(mcp, {"datastore": ns, "row": {"email": "a@x.fr"},
                              "via": "apollo_match_person", "_run_id": run}, refus=True)
    assert "no successful `apollo_match_person` call" in msg
    msg = await _ecrire(mcp, {"datastore": ns, "row": {"email": "a@x.fr"},
                              "via": "apollo_match_person"}, refus=True)
    assert "needs a run" in msg
    msg = await _ecrire(mcp, {"datastore": ns, "row": {"email": "a@x.fr"},
                              "via": "data_rows", "_run_id": run}, refus=True)
    assert "platform tool" in msg
    assert _revisions(ns_id) == [], "un `via` refusé n'écrit rien"


def test_via_est_decrit_dans_la_description_servie():
    from oto_mcp.datastore import via
    from oto_mcp.tools import datastore as T
    assert "<<via>>" not in (via.sous_via(lambda **k: None).__doc__ or "")
    import inspect
    src = inspect.getsource(T)
    assert "@ds_via.sous_via" in src and "via: <<via>>" in src


# ── 4. l'agrégat par colonne ───────────────────────────────────────────────────

def test_l_agregat_compte_la_valeur_actuelle_de_chaque_cellule_tenue(compte):
    from oto_mcp import db, geste
    from oto_mcp.db import historique
    _, ns_id = _table()
    with geste.portee(geste.CONSOLE, SUB):
        for rid in ("r1", "r2", "r3", "r4"):
            db.datastore_insert_row(ns_id, rid, {"nom": rid})
    with geste.portee(geste.AGENT, SUB, outil="waterfall_email_act"):
        for rid, f in (("r1", "hunter"), ("r2", "hunter"), ("r3", "fullenrich"),
                       ("r4", "hunter")):
            with geste.par_fournisseur(f):
                _maj(ns_id, rid, {"email": f"{rid}@x.fr"})
    # r2 : l'email RÉÉCRIT à la main → c'est la main qui le tient désormais.
    with geste.portee(geste.CONSOLE, SUB):
        _maj(ns_id, "r2", {"email": "fixe@x.fr"})
    # r4 : la ligne supprimée ne compte plus.
    db.datastore_delete_row(ns_id, "r4")
    out = historique.sources_des_colonnes(ns_id)
    assert out["complet"] is True
    assert out["colonnes"]["email"] == [
        {"source": "agent", "outil": "waterfall_email_act", "fournisseur": "fullenrich",
         "cellules": 1},
        {"source": "agent", "outil": "waterfall_email_act", "fournisseur": "hunter",
         "cellules": 1},
        {"source": "console", "outil": None, "fournisseur": None, "cellules": 1},
    ]
    assert out["colonnes"]["nom"] == [
        {"source": "console", "outil": None, "fournisseur": None, "cellules": 3}]


def test_l_agregat_dit_quand_la_fenetre_ne_couvre_pas_tout(compte):
    from oto_mcp import db
    from oto_mcp.db import historique
    _, ns_id = _table()
    for i in range(3):
        db.datastore_insert_row(ns_id, f"r{i}", {"nom": str(i)})
    out = historique.sources_des_colonnes(ns_id, limite=2)
    assert (out["complet"], out["revisions_lues"]) == (False, 2)
    assert out["colonnes"]["nom"][0]["cellules"] == 2


# ── 5. la face REST ────────────────────────────────────────────────────────────

class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@sources.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def rest(compte):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from oto_mcp.api import routes as api_routes
    app = Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None))
    return TestClient(api_routes.RestCallLogger(app))


def test_face_rest_forme_servie_et_hors_perimetre(rest):
    from oto_mcp import db, geste
    ns, ns_id = _table()
    with geste.portee(geste.AGENT, SUB, outil="jev_rows"):
        db.datastore_insert_row(ns_id, "r1", {"nom": "x"})
    r = rest.get(f"/api/datastores/{ns_id}/column-sources",
                 headers={"Authorization": f"Bearer {SUB}"})
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["columns"] == {"nom": [
        {"source": "agent", "tool": "jev_rows", "provider": None, "cells": 1}]}
    assert corps["complete"] is True and corps["revisions_read"] == 1
    assert corps["since"] and corps["retention_days"] >= 1
    _, autre = _table(AUTRE)
    r = rest.get(f"/api/datastores/{autre}/column-sources",
                 headers={"Authorization": f"Bearer {SUB}"})
    assert r.status_code == 404, r.text
