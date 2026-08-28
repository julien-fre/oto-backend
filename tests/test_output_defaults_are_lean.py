"""Le retour resserré est le DÉFAUT (oto-core#36, inversé le 11/08/2026).

`compact=True` était un opt-in. Mesuré : un agent branché en direct sur le MCP ne le
passait JAMAIS — six `serper_search` avec `query` seul sur une seule fiche, six
réponses Google entières. Et il n'avait aucune raison de faire autrement : il ne peut
pas savoir qu'un paramètre existe avant d'avoir lu le schéma, rien ne lui dit qu'il
compte, et le guide qui le pilote ne nomme aucun outil (par choix). **Une économie
qu'il faut connaître pour en bénéficier ne bénéficie à personne** — 83 % du coût
d'une conversation d'enrichissement était dans les sorties d'outils.

Le défaut EST la décision : ces tests le figent, sinon un refactor le remettrait à
l'ancien sans qu'aucune ligne de diff ne l'annonce. Ils vérifient aussi
l'échappatoire (`full=True`), qui est ce qui rend l'inversion acceptable.
"""
import asyncio
from unittest.mock import MagicMock

import pytest


def _tool(module, name: str):
    from fastmcp import FastMCP

    m = FastMCP("t")
    module.register(m)
    return asyncio.run(m.get_tool(name)).fn


_GOOGLE = {
    "searchParameters": {"q": "editions"},
    "knowledgeGraph": {"title": "Gallimard", "description": "maison d'édition"},
    "peopleAlsoAsk": [{"question": "qui possède Gallimard ?"}],
    "relatedSearches": [{"query": "gallimard jeunesse"}],
    "organic": [{"title": "Gallimard", "link": "https://x.fr", "snippet": "…",
                 "sitelinks": [{"title": "Contact"}], "attributes": {"a": 1},
                 "imageUrl": "https://img"}],
    "credits": 1,
}


@pytest.fixture
def serper(monkeypatch):
    from oto_mcp.tools import serper as mod

    inst = MagicMock()
    inst.search.return_value = dict(_GOOGLE)
    monkeypatch.setattr("oto.tools.serper.SerperClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda p, account=None: ("k", False))
    return _tool(mod, "serper_search")


def test_search_drops_the_unread_sections_by_default(serper):
    out = serper(query="editions")
    for gone in ("knowledgeGraph", "peopleAlsoAsk", "relatedSearches",
                 "searchParameters"):
        assert gone not in out, f"{gone} devrait tomber par défaut"
    # ce qu'un agent lit reste, enveloppe comprise
    assert out["organic"][0]["title"] == "Gallimard"
    assert out["organic"][0]["snippet"] == "…"
    assert out["credits"] == 1
    for gone in ("sitelinks", "attributes", "imageUrl"):
        assert gone not in out["organic"][0]


def test_full_gives_the_whole_answer_back(serper):
    """L'échappatoire : c'est elle qui rend le défaut acceptable."""
    out = serper(query="editions", full=True)
    assert out["knowledgeGraph"]["title"] == "Gallimard"
    assert out["organic"][0]["sitelinks"] == [{"title": "Contact"}]
    assert out["relatedSearches"] and out["searchParameters"]


def test_fields_still_narrows_further_even_with_full(serper):
    out = serper(query="editions", full=True, fields=["title", "link"])
    assert set(out["organic"][0]) == {"title", "link"}
    assert out["knowledgeGraph"]["title"] == "Gallimard"   # full garde l'enveloppe


@pytest.fixture
def hunter(monkeypatch):
    from oto_mcp.tools import hunter as mod

    inst = MagicMock()
    inst.domain_search.return_value = {"data": {"domain": "gallimard.fr", "emails": [
        {"value": "a@gallimard.fr", "first_name": "A",
         "sources": [{"uri": "https://p1"}, {"uri": "https://p2"}],
         "verification": {"status": "valid", "date": "2026-08-01"}}]}}
    # Le module importe depuis `…hunter.client` (import au register) : patcher
    # `oto.tools.hunter.HunterClient` ne serait pas vu, et l'appel partirait EN RÉSEAU.
    monkeypatch.setattr("oto.tools.hunter.client.HunterClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda p, account=None: ("k", False))
    return _tool(mod, "hunter_domain_search")


def test_domain_search_drops_provenance_by_default(hunter):
    mail = hunter(domain="gallimard.fr")["data"]["emails"][0]
    assert mail["value"] == "a@gallimard.fr" and mail["first_name"] == "A"
    assert "sources" not in mail and "verification" not in mail


def test_domain_search_full_keeps_provenance(hunter):
    """Le besoin RGPD « d'où vient cette adresse » reste servi, à la demande."""
    mail = hunter(domain="gallimard.fr", full=True)["data"]["emails"][0]
    assert len(mail["sources"]) == 2
    assert mail["verification"]["status"] == "valid"
