"""Les outils Notion de la surface complète : aiguillage des ops et traduction
des erreurs du client.

Ce que ce fichier verrouille :
- chaque appel au client passe par la traduction : une `ValueError` du client
  (id qui n'est pas un id Notion, mauvais genre d'objet, base à plusieurs data
  sources) devient INVALID_PARAMS avec SON message — jamais « Erreur interne » ;
- une erreur amont qui n'est pas une `ValueError` n'est PAS déguisée en mauvais
  argument ;
- `notion_view` et `notion_edit_comment` aiguillent vers la bonne méthode et
  refusent un op inconnu ou un paramètre manquant avant tout appel ;
- `notion_get_markdown` borne ce qu'il rend et le dit.
Doublure du client : aucun appel réel à Notion."""
from __future__ import annotations

import pytest
from mcp.types import INVALID_PARAMS

from oto_mcp import access
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import notion as N

NEUFS = {"notion_add_comment", "notion_create_database", "notion_delete_block",
         "notion_edit_comment", "notion_edit_markdown", "notion_get_comments",
         "notion_get_markdown", "notion_list_users", "notion_move_page",
         "notion_update_block", "notion_update_database", "notion_view"}


class _MCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(f):
            self.tools[f.__name__] = f
            return f
        return deco


class _Faux:
    """Enregistre chaque appel ; `leve` fait échouer la méthode nommée."""

    def __init__(self, leve=None, rend=None):
        self.appels, self.leve, self.rend = [], leve or {}, rend or {}

    def __getattr__(self, name):
        def methode(*args, **kwargs):
            self.appels.append((name, args, kwargs))
            if name in self.leve:
                raise self.leve[name]
            return self.rend.get(name, {"ok": name})
        return methode


@pytest.fixture
def outils(monkeypatch):
    import oto.tools.notion.lib.notion_client as nc
    etat = {"client": _Faux()}
    monkeypatch.setattr(access, "resolve_api_key", lambda p, **k: ("secret_x", False))
    monkeypatch.setattr(nc, "NotionClient", lambda **kw: etat["client"])
    monkeypatch.setattr(N.connector_verify, "register", lambda *a, **k: None)
    mcp = _MCP()
    N.register(mcp)

    def avec(client):
        etat["client"] = client
        return mcp.tools, client
    return avec


def test_les_outils_neufs_sont_montes(outils):
    tools, _ = outils(_Faux())
    assert NEUFS <= set(tools)


# --- traduction des erreurs ---------------------------------------------------

def test_base_a_plusieurs_sources_rend_la_liste_pas_une_erreur_interne(outils):
    msg = "Database d1 has 2 data sources: 2025 (s1), 2026 (s2). Pass the id of the data source to use."
    tools, _ = outils(_Faux(leve={"query_database": ValueError(msg)}))
    with pytest.raises(McpError) as exc:
        tools["notion_query_database"](database_id="d1")
    assert exc.value.error.code == INVALID_PARAMS and "s1" in exc.value.error.message


def test_la_liste_des_vues_traduit_aussi(outils):
    tools, _ = outils(_Faux(leve={"list_views": ValueError("is a PAGE, not a database")}))
    with pytest.raises(McpError, match="is a PAGE") as exc:
        tools["notion_view"](op="list", database_id="d1")
    assert exc.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize("nom, kw, methode", [
    ("notion_get_page", {"page_id": "../x"}, "get_page"),
    ("notion_get_blocks", {"page_id": "../x"}, "get_page_blocks"),
    ("notion_delete_block", {"block_id": "../x"}, "delete_block"),
    ("notion_get_markdown", {"page_id": "../x"}, "get_markdown"),
    ("notion_get_comments", {"page_id": "../x"}, "list_comments"),
])
def test_un_id_refuse_par_le_client_devient_invalid_params(outils, nom, kw, methode):
    tools, _ = outils(_Faux(leve={methode: ValueError("page_id '../x': not a Notion id")}))
    with pytest.raises(McpError, match="not a Notion id") as exc:
        tools[nom](**kw)
    assert exc.value.error.code == INVALID_PARAMS


def test_une_erreur_amont_n_est_pas_un_mauvais_argument(outils):
    class Amont(Exception):
        status = 503

    tools, _ = outils(_Faux(leve={"get_page": Amont("Notion API error (503)")}))
    with pytest.raises(Amont):
        tools["notion_get_page"](page_id="p")


# --- aiguillage ----------------------------------------------------------------

@pytest.mark.parametrize("kw, methode", [
    ({"op": "list", "database_id": "d1"}, "list_views"),
    ({"op": "get", "view_id": "v1"}, "get_view"),
    ({"op": "create", "database_id": "d1", "name": "Tous"}, "create_view"),
    ({"op": "update", "view_id": "v1", "name": "Tous"}, "update_view"),
    ({"op": "delete", "view_id": "v1"}, "delete_view"),
])
def test_notion_view_aiguille(outils, kw, methode):
    tools, client = outils(_Faux())
    tools["notion_view"](**kw)
    assert [a[0] for a in client.appels] == [methode]


@pytest.mark.parametrize("kw, attendu", [
    ({"op": "list"}, "database_id"),
    ({"op": "get"}, "view_id"),
    ({"op": "create", "database_id": "d1"}, "name"),
    ({"op": "rename", "view_id": "v1"}, "expected list"),
])
def test_notion_view_refuse_avant_tout_appel(outils, kw, attendu):
    tools, client = outils(_Faux())
    with pytest.raises(McpError, match=attendu):
        tools["notion_view"](**kw)
    assert client.appels == []


def test_notion_edit_comment_aiguille(outils):
    tools, client = outils(_Faux())
    tools["notion_edit_comment"](comment_id="c1", delete=True)
    tools["notion_edit_comment"](comment_id="c1", text="corrigé")
    assert [a[0] for a in client.appels] == ["delete_comment", "update_comment"]
    with pytest.raises(McpError, match="`text`"):
        tools["notion_edit_comment"](comment_id="c1")


# --- borne du Markdown -------------------------------------------------------

def test_markdown_borne_et_le_dit(outils, monkeypatch):
    monkeypatch.setattr(N, "_MAX_MARKDOWN", 10)
    tools, _ = outils(_Faux(rend={"get_markdown": {"markdown": "x" * 25, "truncated": False}}))
    r = tools["notion_get_markdown"](page_id="p")
    assert r["markdown"] == "x" * 10 and r["oto_truncated"] is True
    assert r["markdown_chars"] == 25


def test_markdown_court_rendu_tel_quel(outils):
    rep = {"markdown": "# Titre", "truncated": False}
    tools, _ = outils(_Faux(rend={"get_markdown": rep}))
    assert tools["notion_get_markdown"](page_id="p") == rep
