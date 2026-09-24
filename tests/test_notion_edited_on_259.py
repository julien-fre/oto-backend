"""`notion_search(edited_on=…)` sert `search_edited_on` (otomata-tech/oto#259).

La méthode existait en bibliothèque depuis oto-core v1.103.0 — « qu'est-ce qui a
changé ce jour-là » calculé et non inféré — et aucun outil ne l'appelait : présente
et inerte. Ces tests vérifient que le paramètre la sert, avec la fenêtre, le type
et la requête passés tels quels ; qu'une date mal formée est un refus d'ENTRÉE (pas
une panne) ; et qu'un zéro sur un jour porte l'indice du partage. Doublure du
client : aucun appel réel à Notion."""
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
    def __init__(self, objets):
        self.objets, self.appels = objets, []

    def search(self, *a, **k):
        raise AssertionError("edited_on doit passer par search_edited_on, pas search")

    def search_edited_on(self, date, filter_type=None, query="", max_pages=50):
        self.appels.append((date, filter_type, query))
        if date == "hier":
            raise ValueError("date invalide 'hier', attendu 'YYYY-MM-DD'")
        return self.objets


@pytest.fixture
def outil(monkeypatch):
    import oto.tools.notion.lib.notion_client as nc
    etat = {}
    monkeypatch.setattr(access, "resolve_api_key", lambda p, **k: ("secret_x", False))
    monkeypatch.setattr(nc, "NotionClient", lambda **kw: etat["client"])
    monkeypatch.setattr(N.connector_verify, "register", lambda *a, **k: None)
    mcp = _MCP()
    N.register(mcp)

    def appeler(objets, **kw):
        etat["client"] = _FauxClient(objets)
        return mcp.tools["notion_search"](**kw), etat["client"]
    return appeler


def test_edited_on_sert_la_methode_de_bibliotheque(outil):
    objets = [{"object": "page", "id": "p1", "last_edited_time": "2026-09-04T17:00:00.000Z"}]
    r, client = outil(objets, query="", filter_type="page", edited_on="2026-09-04")
    assert client.appels == [("2026-09-04", "page", "")]
    assert r == {"results": objets, "edited_on": "2026-09-04"}


def test_une_date_mal_formee_est_un_refus_d_entree(outil):
    with pytest.raises(McpError) as exc:
        outil([], query="", edited_on="hier")
    assert "YYYY-MM-DD" in str(exc.value)


def test_zero_sur_un_jour_dit_comment_trancher(outil):
    r, _ = outil([], query="", edited_on="2026-09-04")
    w = r.get("warning")
    assert w and "2026-09-04" in w and "partag" in w and 'query=""' in w
    assert "edited_on" in w, "le geste discriminant doit dire de retirer edited_on"
