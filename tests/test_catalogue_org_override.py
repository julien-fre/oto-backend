"""`catalogue_avec_etat` peut être appelé pour une AUTRE org que celle de la
session — né du 16/09/2026 : `oto_trigger` vérifie la joignabilité des outils
déclarés d'un déclencheur pour l'org du TRAVAIL, jamais celle de qui pose la
question (`runner_triggers._avec_tool_warnings`). Sans un `org=` explicite,
`compute_hidden_layers` dérive l'org de la SESSION (`current_org(sub)`) — exactement
le calcul qui manquait à la poignée de main MCP et a rendu deux outils actifs (mais
sélectionnés pour une AUTRE org) invisibles sans un mot (feedback #1021).

Ce banc n'exerce PAS la logique de masquage elle-même (`test_catalogue_complet_avec_etat.py`
la couvre déjà, en détail) — seulement que `org=` VOYAGE de `catalogue_avec_etat`
jusqu'à `compute_hidden_layers`, au lieu d'être tacitement ignoré.
"""
from __future__ import annotations

import types

import pytest

from oto_mcp import session_visibility as sv
from oto_mcp.tools import catalogue


@pytest.mark.asyncio
async def test_org_explicite_voyage_jusqu_a_compute_hidden_layers(monkeypatch):
    vu = {}

    async def _faux_provider_list_tools(_fastmcp):
        return []

    async def _faux_compute_hidden_layers(ctx, sub, *, org=sv._DERIVE_ORG):
        vu["org"] = org
        return {}

    from fastmcp.server.providers.base import Provider
    monkeypatch.setattr(Provider, "list_tools", staticmethod(_faux_provider_list_tools))
    monkeypatch.setattr(catalogue.session_visibility, "compute_hidden_layers",
                        _faux_compute_hidden_layers)

    ctx = types.SimpleNamespace(fastmcp=None)
    await catalogue.catalogue_avec_etat(ctx, "u1", "", org=178)
    assert vu["org"] == 178, "l'org explicite doit atteindre compute_hidden_layers telle quelle"


@pytest.mark.asyncio
async def test_sans_org_explicite_le_defaut_reste_la_derivation_de_session(monkeypatch):
    """Le comportement d'avant ce lot, préservé : l'appelant historique
    (`oto_list_my_tools`) n'a jamais donné `org=`, et ne doit pas se mettre à
    dériver autrement."""
    vu = {}

    async def _faux_provider_list_tools(_fastmcp):
        return []

    async def _faux_compute_hidden_layers(ctx, sub, *, org=sv._DERIVE_ORG):
        vu["org"] = org
        return {}

    from fastmcp.server.providers.base import Provider
    monkeypatch.setattr(Provider, "list_tools", staticmethod(_faux_provider_list_tools))
    monkeypatch.setattr(catalogue.session_visibility, "compute_hidden_layers",
                        _faux_compute_hidden_layers)

    ctx = types.SimpleNamespace(fastmcp=None)
    await catalogue.catalogue_avec_etat(ctx, "u1", "")
    assert vu["org"] is sv._DERIVE_ORG, "sans org=, le sentinel de dérivation doit passer inchangé"
