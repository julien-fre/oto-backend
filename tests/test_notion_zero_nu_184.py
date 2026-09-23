"""`notion_search` ne rend plus un zéro NU (otomata-tech/oto#184).

Un jeton Notion valide auquel rien n'est partagé répond `{"results": [],
"has_more": false}` — la même chose qu'un espace qui ne contient pas ce qu'on
cherche — et la sonde reste verte. Mesuré : douze matins de suite à zéro sur un
credential présent, chaque run redécouvrant le même fait. Le zéro porte donc
l'indice, formulé comme une possibilité à VÉRIFIER (la distinction n'a pas été
éprouvée contre l'API) et le geste qui la lève. Doublure du client : aucun appel
réel à Notion."""
from __future__ import annotations

import pytest

from oto_mcp import access
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

    def search(self, query, filter_type=None, sort="relevance"):
        self.appels.append((query, filter_type, sort))
        return self.reponse


@pytest.fixture
def search(monkeypatch):
    import oto.tools.notion.lib.notion_client as nc
    etat = {}
    monkeypatch.setattr(access, "resolve_api_key", lambda p, **k: ("secret_x", False))
    monkeypatch.setattr(nc, "NotionClient", lambda **kw: etat["client"])
    monkeypatch.setattr(N.connector_verify, "register", lambda *a, **k: None)
    mcp = _MCP()
    N.register(mcp)

    def appeler(reponse, **kw):
        etat["client"] = _FauxClient(reponse)
        return mcp.tools["notion_search"](**kw)
    return appeler


def test_zero_sans_filtre_nomme_le_partage_et_le_geste(search):
    r = search({"results": [], "has_more": False}, query="")
    assert r["results"] == [] and r["has_more"] is False
    w = r.get("warning")
    assert w, "un zéro nu : l'agent ne peut distinguer « rien de partagé » d'un espace vide"
    assert "partag" in w and "intégration" in w
    assert "vraisemblablement" in w and "vérifier" in w.lower(), (
        "l'indice doit rester une possibilité, pas un diagnostic affirmé")


def test_zero_sur_une_requete_dit_comment_trancher(search):
    r = search({"results": [], "has_more": False}, query="roadmap")
    w = r.get("warning")
    assert w and 'query=""' in w, "le geste discriminant (requête vide) doit être nommé"
    assert "partag" in w


def test_zero_filtre_par_type_dit_aussi_comment_trancher(search):
    w = search({"results": []}, query="", filter_type="database").get("warning")
    assert w and 'query=""' in w and "filter_type" in w


def test_un_resultat_non_vide_reste_intact(search):
    rep = {"results": [{"object": "page", "id": "p1"}], "has_more": False}
    assert search(rep, query="roadmap") == rep
