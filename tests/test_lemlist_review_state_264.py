"""Créer un lead ne dit PAS s'il part (otomata-tech/oto#264).

La réponse de lemlist à `POST /campaigns/{id}/leads/` porte `isPaused`, qui reste
`false` qu'un lead soit retenu en revue ou lancé (mesuré le 04/09/2026) : rendu brut,
il se lisait « l'envoi part ». Le tool l'écarte, dit `review_state: "unknown"` et
renvoie vers les compteurs de `lemlist_campaign(op="reports")`, seuls à trancher.
Doublure du client lemlist, aucun appel réel."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

_BRUT = {"_id": "lea_1", "campaignId": "cam_1", "campaignName": "Vague",
         "email": "a@b.co", "isPaused": False}


class _Faux:
    def __init__(self, api_key=None):
        pass

    def create_lead(self, campaign_id, lead, **flags):
        return dict(_BRUT)


@pytest.fixture()
def creer():
    from fastmcp import FastMCP
    from oto_mcp.tools import lemlist

    m = FastMCP("t")
    with patch("oto_mcp.access.resolve_api_key", return_value=("k", False)), \
            patch("oto.tools.lemlist.LemlistClient", _Faux):
        lemlist.register(m)
        fn = asyncio.run(m.get_tool("lemlist_create_lead")).fn
        yield lambda: fn(campaign_id="cam_1", email="a@b.co")


def test_l_etat_de_revue_est_dit_inconnu(creer):
    r = creer()
    assert r["review_state"] == "unknown"
    assert "reports" in r["review_hint"]
    assert "reviewedCount" in r["review_hint"] and "inSequenceLeadCount" in r["review_hint"]


def test_ispaused_n_est_plus_servi(creer):
    """Le champ qui se lisait « l'envoi part » ne traverse plus."""
    assert "isPaused" not in creer()


def test_le_reste_du_lead_traverse(creer):
    r = creer()
    assert {k: r[k] for k in ("_id", "campaignId", "campaignName", "email")} == {
        k: _BRUT[k] for k in ("_id", "campaignId", "campaignName", "email")}


def test_le_texte_servi_renvoie_vers_les_compteurs():
    from fastmcp import FastMCP
    from oto_mcp.tools import lemlist

    m = FastMCP("t")
    lemlist.register(m)
    doc = asyncio.run(m.get_tool("lemlist_create_lead")).description
    assert "review_state" in doc and 'op="reports"' in doc
    assert "reviewedCount" in doc and "inSequenceLeadCount" in doc
