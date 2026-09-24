"""Un connecteur coupé n'est servi par AUCUN chemin d'appel (oto-backend#1064).

Mesuré le 24/09/2026 sur le serveur réellement monté : le connecteur coupé (master
plateforme OFF, ou override d'org OFF), son outil était refusé en appel direct — par
la visibilité de session, fail-open, avec un message « pas installé » qui renvoyait
vers `oto_call` — et **servi** par `oto_call`, handler exécuté. `SECURITY.md` §4 et
le docstring d'`oto_call` annonçaient pourtant une garde d'appel.

Ce qu'on garde vert, sur le chemin SERVI (`_build_mcp`, client MCP en mémoire, vrai
PostgreSQL, sub tenu par `OTO_MCP_DEV_SUB`) :

  1. master OFF, override d'org OFF, coupure d'équipe : refus `connector_disabled`
     sur l'appel direct ET par `oto_call`, handler jamais atteint ;
  2. connecteur ouvert : le handler est atteint par les deux chemins (sans ce témoin,
     un refus pour une autre raison passerait pour la garde) ;
  3. la garde lit l'org sous laquelle la cible RÉSOUT (`_org=` dans `arguments`), pas
     l'org maison — dans les deux sens.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.types import INVALID_PARAMS, ErrorData

from oto_mcp.mcp_errors import McpError

SUB = "sub-activation-1064"
CONNECTEUR = "serper"
OUTIL = "serper_search"
SONDE = "SONDE-1064 : handler atteint"


@pytest.fixture(scope="module")
def orgs(live) -> dict:
    """Deux orgs réelles du même sub : `maison` (active) et `autre`, plus une équipe
    de la maison."""
    from oto_mcp import group_store, org_store
    maison = org_store.create_org("Maison 1064", created_by=SUB)
    autre = org_store.create_org("Autre 1064", created_by=SUB)
    for o in (maison, autre):
        org_store.add_org_member(o, SUB)
    assert org_store.set_active_org(SUB, maison)
    equipe = group_store.create_group(maison, "Equipe 1064")
    group_store.add_group_member(equipe, SUB)
    return {"maison": maison, "autre": autre, "equipe": equipe}


@pytest.fixture
def etat(orgs, monkeypatch):
    """Le connecteur ouvert partout et installé chez le sub ; le handler remplacé
    par une sonde qui dit qu'il a été atteint (aucun appel amont)."""
    from oto_mcp import access, group_store
    from oto_mcp.connectors import activation, selection

    monkeypatch.setenv("OTO_MCP_DEV_SUB", SUB)

    def _sonde(*_a, **_k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message=SONDE))
    monkeypatch.setattr(access, "resolve_api_key", _sonde)

    activation.set_activation(CONNECTEUR, True, None)
    for o in (orgs["maison"], orgs["autre"]):
        activation.clear_activation(CONNECTEUR, o)
        selection.set_state(SUB, CONNECTEUR, selection.ACTIVE, o)
    activation.clear_group_activation(orgs["equipe"], CONNECTEUR)
    group_store.clear_active_group(SUB)
    yield orgs
    activation.set_activation(CONNECTEUR, True, None)
    activation.clear_group_activation(orgs["equipe"], CONNECTEUR)
    group_store.clear_active_group(SUB)


def _appeler(chemin: str, arguments: dict) -> str:
    """Le texte rendu au client MCP — résultat ou refus — pour un appel `direct`
    de l'outil ou via `oto_call`."""
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from _mcp_app import static_mcp

    async def go() -> str:
        async with Client(static_mcp()) as c:
            try:
                if chemin == "direct":
                    r = await c.call_tool(OUTIL, arguments)
                else:
                    r = await c.call_tool("oto_call", {"name": OUTIL, "arguments": arguments})
            except ToolError as e:
                return str(e)
            return r.content[0].text if r.content else str(r.structured_content)
    return asyncio.run(go())


CHEMINS = ("direct", "oto_call")


def _refuse(texte: str) -> bool:
    return "connector_disabled" in texte and SONDE not in texte


@pytest.mark.parametrize("chemin", CHEMINS)
def test_connecteur_ouvert_le_handler_est_atteint(etat, chemin):
    texte = _appeler(chemin, {"query": "x"})
    assert SONDE in texte and "connector_disabled" not in texte, texte


@pytest.mark.parametrize("chemin", CHEMINS)
def test_master_plateforme_off_refuse(etat, chemin):
    from oto_mcp.connectors import activation
    activation.set_activation(CONNECTEUR, False, None)

    texte = _appeler(chemin, {"query": "x"})

    assert _refuse(texte), texte
    assert "plateforme" in texte


@pytest.mark.parametrize("chemin", CHEMINS)
def test_override_d_org_off_refuse(etat, chemin):
    from oto_mcp.connectors import activation
    activation.set_activation(CONNECTEUR, False, etat["maison"])

    texte = _appeler(chemin, {"query": "x"})

    assert _refuse(texte), texte
    # Le geste qui rouvre est nommé, avec l'org et le connecteur.
    assert (f"oto_connector_activation(op='set', scope='org', org_id={etat['maison']}, "
            f"name='{CONNECTEUR}', enabled=true)") in texte


@pytest.mark.parametrize("chemin", CHEMINS)
def test_coupure_d_equipe_refuse(etat, chemin):
    from oto_mcp import group_store
    from oto_mcp.connectors import activation
    assert group_store.set_active_group(SUB, etat["equipe"])
    activation.set_group_activation(etat["equipe"], CONNECTEUR, False)

    texte = _appeler(chemin, {"query": "x"})

    assert _refuse(texte), texte
    assert f"group_id={etat['equipe']}" in texte


def test_la_garde_lit_l_org_de_resolution_pas_la_maison(etat):
    """Maison coupée, `_org=` vers une org ouverte : servi. Et l'inverse : refusé."""
    from oto_mcp.connectors import activation
    activation.set_activation(CONNECTEUR, False, etat["maison"])
    texte = _appeler("oto_call", {"query": "x", "_org": etat["autre"]})
    assert SONDE in texte and "connector_disabled" not in texte, texte

    activation.clear_activation(CONNECTEUR, etat["maison"])
    activation.set_activation(CONNECTEUR, False, etat["autre"])
    texte = _appeler("oto_call", {"query": "x", "_org": etat["autre"]})
    assert _refuse(texte), texte
    assert f"org_id={etat['autre']}" in texte
