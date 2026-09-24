"""`notion_search` se pagine : le curseur de Notion se repasse (otomata-tech/oto#249).

Notion rend au plus 100 objets par `search`, avec `has_more` et `next_cursor` —
mais aucun paramètre de l'outil ne permettait de repasser ce curseur. Mesuré le
04/09/2026 : une requête vide triée par date d'édition ne remontait qu'à la
mi-journée, le reste du jour restait hors d'atteinte. Liste paginée ⟹ projection
`fields` (cliquet `test_sorties_listes_projetees`). Doublure du client : aucun
appel réel à Notion."""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import notion as N


class _MCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(f):
            self.tools[f.__name__] = f
            return f
        return deco


class _FauxClient:
    def __init__(self, reponse):
        self.reponse, self.appels = reponse, []

    def search(self, query, filter_type=None, sort="relevance", start_cursor=None):
        self.appels.append({"query": query, "filter_type": filter_type, "sort": sort,
                            "start_cursor": start_cursor})
        return self.reponse

    def search_edited_on(self, *a, **k):
        raise AssertionError("edited_on + cursor doit être refusé avant tout appel")


@pytest.fixture
def outil(monkeypatch):
    import oto.tools.notion.lib.notion_client as nc
    etat = {}
    monkeypatch.setattr(access, "resolve_api_key", lambda p, **k: ("secret_x", False))
    monkeypatch.setattr(nc, "NotionClient", lambda **kw: etat["client"])
    monkeypatch.setattr(N.connector_verify, "register", lambda *a, **k: None)
    mcp = _MCP()
    N.register(mcp)

    def appeler(reponse, **kw):
        etat["client"] = _FauxClient(reponse)
        return mcp.tools["notion_search"](**kw), etat["client"]
    return appeler


_PAGE = {"object": "page", "id": "p1", "url": "https://notion.so/p1",
         "last_edited_time": "2026-09-04T12:00:00.000Z",
         "properties": {"title": {"title": [{"plain_text": "Roadmap"}]}}}


def test_le_curseur_est_repasse_au_client(outil):
    rep = {"results": [_PAGE], "has_more": True, "next_cursor": "cur-3"}
    r, client = outil(rep, query="", sort="last_edited_time", cursor="cur-2")
    assert client.appels == [{"query": "", "filter_type": None,
                              "sort": "last_edited_time", "start_cursor": "cur-2"}]
    assert r == rep, "sans `fields`, la réponse reste intacte"


def test_fields_resserre_les_objets_et_garde_l_enveloppe(outil):
    rep = {"results": [_PAGE], "has_more": True, "next_cursor": "cur-3"}
    r, _ = outil(rep, query="", fields=["id", "last_edited_time"])
    assert r["results"] == [{"id": "p1", "last_edited_time": "2026-09-04T12:00:00.000Z"}]
    assert r["has_more"] is True and r["next_cursor"] == "cur-3", (
        "sans le curseur dans l'enveloppe, l'agent croit avoir tout vu")


def test_edited_on_ne_se_pagine_pas(outil):
    with pytest.raises(McpError) as exc:
        outil({}, query="", edited_on="2026-09-04", cursor="cur-2")
    assert "cursor" in str(exc.value)
