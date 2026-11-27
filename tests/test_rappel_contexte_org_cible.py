"""Le rappel du contexte d'org nomme l'org de l'APPEL, pas l'org par défaut du compte.

Le défaut corrigé : sous `oto_call`, les axes de la cible (`_org=`, `_project=` dans
`arguments`) ne sont posés que DANS son handler (`executer_cible`) et retirés avant
qu'il rende. Le verdict, pris avant l'appel, lisait donc l'org par défaut du compte —
qui suit le dernier workspace ouvert — et chaque réponse d'un agent qui travaillait,
épinglé, dans une org X portait « le contexte de l'org Y est non lu » : l'appel
semblait parti dans la mauvaise org, et l'agent était poussé à lire le contexte d'une
org où il ne travaillait pas. Une lecture de schéma (`oto_tool_schema`), qui n'agit
dans aucune org, portait la même ligne.

Sur le chemin SERVI (vraie chaîne de middlewares autour d'un vrai `oto_call`, vrai
PostgreSQL : README, journal des lectures) :
  1. `oto_call` épinglé sur `travail`, maison non lue : la ligne nomme `travail` ;
  2. `travail` lu, maison non lue : aucune ligne ;
  3. sans `oto_call` ni `_org`, l'appel direct reste sous l'org par défaut ;
  4. `oto_tool_schema` ne porte jamais de ligne.
"""
from __future__ import annotations

import pytest

from oto_mcp import rappel_contexte

SUB = "sub-rappel-org-cible"


@pytest.fixture(autouse=True)
def _cache_vide():
    rappel_contexte._cache.clear()
    yield
    rappel_contexte._cache.clear()


def _readme(org_id: int, corps: str) -> None:
    from oto_mcp import db
    db.set_init_guide_db("org", str(org_id), "readme", corps)


def _vieillir(org_id: int, secondes: int) -> None:
    from oto_mcp.db import _connect
    with _connect() as conn:
        conn.execute("UPDATE nodes SET updated_at = NOW() - make_interval(secs => %s) "
                     "WHERE owner_type = 'org' AND owner_id = %s "
                     "AND props->>'slug' = 'readme'", (secondes, str(org_id)))


def _lecture(org_id: int) -> None:
    from oto_mcp import db
    db.insert_tool_call({"tool": "oto_context", "sub": SUB, "kind": "mcp", "ok": True,
                         "org_id": org_id, "duration_ms": 1})


@pytest.fixture
def orgs(live, monkeypatch):
    """Deux orgs RÉELLES du même compte, README posé sur chacune, aucune lue : `maison`
    (l'org par défaut du compte) et `travail` (celle où l'agent épingle ses appels).
    Le serveur est monté AVANT de tenir l'identité : `register_all` importe des modules
    qui lient le lecteur d'identité à l'import (#1111)."""
    import uuid

    from oto_mcp import access, call_axes, org_store
    from oto_mcp.auth import hooks
    from oto_mcp.middleware import call_context
    from oto_mcp.tools import meta
    mcp = _serveur()
    u = uuid.uuid4().hex[:6]
    maison = org_store.create_org(f"Maison {u}", created_by=SUB)
    travail = org_store.create_org(f"Travail {u}", created_by=SUB)
    for o in (maison, travail):
        org_store.add_org_member(o, SUB)
        _readme(o, "Règle de l'org : valider avant d'envoyer.")
    assert org_store.set_active_org(SUB, maison)
    for mod in (hooks, call_axes, meta, access, call_context):
        monkeypatch.setattr(mod, "current_user_sub_from_token", lambda: SUB,
                            raising=False)
    monkeypatch.setattr(hooks, "current_token_axes", lambda: {})
    return {"maison": maison, "travail": travail, "mcp": mcp}


def _serveur():
    """Le registre du boot (`register_all`), une sonde de connecteur, et les trois
    étages du rappel dans l'ordre de production : pose, contexte d'appel, verdict."""
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.middleware.call_context import CallContextMiddleware
    from oto_mcp.middleware.rappel_contexte import (ConstatContexteMiddleware,
                                                    RappelContexteMiddleware)
    from oto_mcp.tools import register_all
    mcp = FastMCP("t-rappel-org-cible")
    register_all(mcp)

    @mcp.tool()
    def serper_sonde() -> dict:
        return {"org": access.current_org(SUB)}

    mcp.add_middleware(RappelContexteMiddleware())
    mcp.add_middleware(CallContextMiddleware(frozenset()))
    mcp.add_middleware(ConstatContexteMiddleware())
    return mcp


def _rappels(res) -> list[str]:
    return [b.text for b in res.content
            if getattr(b, "text", "").startswith("⚠️ oto — the context of org")]


@pytest.mark.asyncio
async def test_oto_call_epingle_nomme_l_org_de_la_cible(orgs):
    from fastmcp import Client
    async with Client(orgs["mcp"]) as c:
        res = await c.call_tool("oto_call", {"name": "serper_sonde",
                                             "arguments": {"_org": orgs["travail"]}})
    assert res.structured_content["org"] == orgs["travail"], res
    rappels = _rappels(res)
    assert len(rappels) == 1, res
    assert f"`_org={orgs['travail']}`" in rappels[0]
    assert f"`_org={orgs['maison']}`" not in rappels[0]


@pytest.mark.asyncio
async def test_l_org_de_la_cible_lue_aucun_rappel_meme_maison_non_lue(orgs):
    from fastmcp import Client
    _vieillir(orgs["travail"], 60)
    _lecture(orgs["travail"])
    async with Client(orgs["mcp"]) as c:
        res = await c.call_tool("oto_call", {"name": "serper_sonde",
                                             "arguments": {"_org": orgs["travail"]}})
    assert res.structured_content["org"] == orgs["travail"], res
    assert _rappels(res) == []


@pytest.mark.asyncio
async def test_un_appel_direct_reste_sous_l_org_par_defaut(orgs):
    """Sans `oto_call` ni `_org`, rien ne change : l'org de l'appel EST la maison."""
    from fastmcp import Client
    async with Client(orgs["mcp"]) as c:
        res = await c.call_tool("serper_sonde", {})
    rappels = _rappels(res)
    assert len(rappels) == 1 and f"`_org={orgs['maison']}`" in rappels[0]


@pytest.mark.asyncio
async def test_une_lecture_de_schema_ne_porte_jamais_le_rappel(orgs):
    """`oto_tool_schema` n'agit dans aucune org (et `oto_call` refuse de la dispatcher) :
    la maison non lue n'y est pas rappelée."""
    from fastmcp import Client
    async with Client(orgs["mcp"]) as c:
        res = await c.call_tool("oto_tool_schema", {"name": "serper_sonde"})
    assert not res.is_error, res
    assert _rappels(res) == []
