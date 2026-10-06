"""Tools `sharepoint_*` du connecteur.

Ce que ce fichier verrouille :
- la SURFACE (2 tools) et le routage vers la bonne méthode de `GraphClient` ;
- le jeton : celui de la personne appelante (`auth/microsoft.access_token_for`),
  seul passé au client ; pas de compte connecté → refus qui dit le geste, avant de
  construire le client ;
- le drive : le OneDrive de la personne par défaut, une bibliothèque par
  `drive_id`, le OneDrive d'un collaborateur par `user`, jamais les deux ;
- la lecture : Word/PowerPoint convertis en PDF, le reste tel quel, rendu par
  `file_content.render_for_agent` ; un dossier ou un fichier trop gros refusé ;
- le dépôt : texte ou base64 (un seul), `conflict` transmis, type deviné ;
- la vue resserrée par défaut, l'objet Graph brut avec `full=True` ;
- un 403 de Graph → refus nommé « droits », un 429 laissé réessayable.
"""
import asyncio
import base64
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def construits(monkeypatch):
    """Les arguments de chaque construction de `GraphClient`, et le faux client.
    La personne appelante a un compte connecté (jeton « AT-sub-1 »)."""
    inst, calls = MagicMock(), []

    def fabrique(*args, **kw):
        assert not kw, "le client se construit avec le seul jeton"
        calls.append(args)
        return inst

    monkeypatch.setattr("oto.tools.microsoft.GraphClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for",
                        lambda sub: f"AT-{sub}")
    return inst, calls


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import sharepoint as X

    m = FastMCP("t")
    X.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _render_capture(monkeypatch):
    vus = []

    def render(data, filename, mime, *, sub, prefix, **kw):
        vus.append({"data": data, "filename": filename, "mime": mime, "sub": sub,
                    "prefix": prefix, **kw})
        return {"encoding": "text", "content": "…"}

    monkeypatch.setattr("oto_mcp.file_content.render_for_agent", render)
    return vus


def _upstream(status, body=None):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body or {"error": {"message": "nope"}},
                             service="microsoft")


# --- surface & jeton ------------------------------------------------------------

def test_surface(construits):
    names = {t.name for t in asyncio.run(_mcp().list_tools())}
    assert names == {"sharepoint_site", "sharepoint_file"}


def test_le_client_porte_le_jeton_de_l_appelant(construits):
    inst, calls = construits
    inst.search_sites.return_value = []
    _tool("sharepoint_site")(query="rh")
    assert calls == [("AT-sub-1",)]


def test_pas_de_compte_connecte_refus_qui_dit_le_geste(construits, monkeypatch):
    def refus(sub):
        raise RuntimeError("No Microsoft account connected. Sign in from your "
                           "connectors page")

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", refus)
    with pytest.raises(McpError, match="Sign in"):
        _tool("sharepoint_site")(query="rh")
    assert construits[1] == []


def test_autorisation_morte_refus_qui_dit_le_geste(construits, monkeypatch):
    from oto_mcp.auth.microsoft import MicrosoftReauthRequired

    def morte(sub):
        raise MicrosoftReauthRequired("Microsoft no longer accepts this sign-in. "
                                      "Reconnect")

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", morte)
    with pytest.raises(McpError, match="Reconnect"):
        _tool("sharepoint_file")()


# --- sites ----------------------------------------------------------------------

def test_site_par_url(construits, monkeypatch):
    inst, _ = construits
    inst.get_site_by_path.return_value = {"id": "s1", "displayName": "Marketing",
                                          "extra": "x"}
    out = _tool("sharepoint_site")(op="get",
                                   url="https://contoso.sharepoint.com/sites/Marketing/")
    inst.get_site_by_path.assert_called_once_with("contoso.sharepoint.com", "sites/Marketing")
    assert out["id"] == "s1" and "extra" not in out


def test_site_racine_par_url(construits, monkeypatch):
    inst, _ = construits
    inst.get_site.return_value = {"id": "root"}
    _tool("sharepoint_site")(op="get", url="https://contoso.sharepoint.com")
    inst.get_site.assert_called_once_with("contoso.sharepoint.com")


def test_drives_d_un_site(construits, monkeypatch):
    inst, _ = construits
    inst.list_site_drives.return_value = [{"id": "d1", "name": "Documents",
                                           "driveType": "documentLibrary"}]
    out = _tool("sharepoint_site")(op="drives", site_id="s1")
    assert out["drives"][0]["id"] == "d1" and out["count"] == 1


def test_site_get_exige_un_seul_designateur(construits, monkeypatch):
    with pytest.raises(McpError, match="only one"):
        _tool("sharepoint_site")(op="get", site_id="s1", url="https://a.sharepoint.com")


# --- drive ----------------------------------------------------------------------

def test_drive_id_et_user_s_excluent(construits, monkeypatch):
    with pytest.raises(McpError, match="not both"):
        _tool("sharepoint_file")(drive_id="d1", user="a@b.fr")


def test_mon_onedrive_par_defaut(construits, monkeypatch):
    inst, _ = construits
    inst.get_my_drive.return_value = {"id": "moi"}
    inst.list_children.return_value = []
    out = _tool("sharepoint_file")()
    inst.list_children.assert_called_once_with("moi", item_id=None, path=None, limit=200)
    inst.get_user_drive.assert_not_called()
    assert out["drive_id"] == "moi"


def test_onedrive_par_user(construits, monkeypatch):
    inst, _ = construits
    inst.get_user_drive.return_value = {"id": "od1"}
    inst.list_children.return_value = [{
        "id": "i1", "name": "Contrats", "folder": {"childCount": 3},
        "parentReference": {"driveId": "od1", "path": "/drive/root:"}}]
    out = _tool("sharepoint_file")(user="marie@contoso.com", path="Docs")
    inst.get_user_drive.assert_called_once_with("marie@contoso.com")
    inst.list_children.assert_called_once_with("od1", item_id=None, path="Docs", limit=200)
    item = out["items"][0]
    assert item["kind"] == "folder" and item["childCount"] == 3
    assert item["folder_path"] == "/" and out["drive_id"] == "od1"


def test_chemin_du_dossier_parent(construits, monkeypatch):
    inst, _ = construits
    inst.get_item.return_value = {"id": "i1", "name": "nda.pdf", "file": {"mimeType": "x"},
                                  "parentReference": {"path": "/drives/d1/root:/Contrats/2026"}}
    assert _tool("sharepoint_file")(op="get", drive_id="d1",
                                    item_id="i1")["folder_path"] == "/Contrats/2026"


# --- lecture --------------------------------------------------------------------

def test_word_lu_en_pdf(construits, monkeypatch):
    inst, _ = construits
    inst.get_item.return_value = {"id": "i1", "name": "NDA v2.docx", "size": 10,
                                  "file": {"mimeType": "application/vnd.openxml"}}
    inst.download.return_value = b"%PDF"
    vus = _render_capture(monkeypatch)
    out = _tool("sharepoint_file")(op="download", drive_id="d1", path="Contrats/NDA v2.docx")
    inst.get_item.assert_called_once_with("d1", item_id=None, path="Contrats/NDA v2.docx")
    inst.download.assert_called_once_with("d1", item_id="i1", format="pdf")
    assert vus[0]["filename"] == "NDA v2.pdf" and vus[0]["mime"] == "application/pdf"
    assert vus[0]["prefix"] == "sharepoint-files" and vus[0]["sub"] == "sub-1"
    assert out["converted_from"] == "docx"


def test_tableur_lu_tel_quel_avec_sa_feuille(construits, monkeypatch):
    inst, _ = construits
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    inst.get_item.return_value = {"id": "i1", "name": "budget.xlsx", "size": 10,
                                  "file": {"mimeType": mime}}
    inst.download.return_value = b"PK"
    vus = _render_capture(monkeypatch)
    out = _tool("sharepoint_file")(op="download", drive_id="d1", item_id="i1", sheet=0)
    inst.download.assert_called_once_with("d1", item_id="i1", format=None)
    assert vus[0]["mime"] == mime and vus[0]["sheet"] == 0
    assert "converted_from" not in out


def test_as_pdf_false_garde_l_original(construits, monkeypatch):
    inst, _ = construits
    inst.get_item.return_value = {"id": "i1", "name": "a.docx", "size": 1, "file": {}}
    inst.download.return_value = b"PK"
    _render_capture(monkeypatch)
    _tool("sharepoint_file")(op="download", drive_id="d1", item_id="i1", as_pdf=False)
    inst.download.assert_called_once_with("d1", item_id="i1", format=None)


def test_dossier_et_gros_fichier_refuses(construits, monkeypatch):
    inst, _ = construits
    inst.get_item.return_value = {"id": "i1", "name": "Contrats", "folder": {}}
    with pytest.raises(McpError, match="folder"):
        _tool("sharepoint_file")(op="download", drive_id="d1", item_id="i1")
    inst.get_item.return_value = {"id": "i2", "name": "film.mp4", "size": 60 * 1024 ** 2,
                                  "file": {}}
    with pytest.raises(McpError, match="50 MB"):
        _tool("sharepoint_file")(op="download", drive_id="d1", item_id="i2")
    inst.download.assert_not_called()


def test_options_de_lecture_hors_download_refusees(construits, monkeypatch):
    with pytest.raises(McpError, match="op='download'"):
        _tool("sharepoint_file")(drive_id="d1", as_pdf=True)


# --- dépôt ----------------------------------------------------------------------

def test_depot_texte(construits, monkeypatch):
    inst, _ = construits
    inst.upload.return_value = {"id": "n1", "name": "cr.md", "file": {}}
    out = _tool("sharepoint_file")(op="upload", drive_id="d1", path="CR", name="cr.md",
                                   content_text="# CR é")
    args, kw = inst.upload.call_args
    assert args == ("d1", "cr.md", "# CR é".encode())
    assert kw["parent_path"] == "CR" and kw["parent_id"] is None
    assert kw["conflict"] == "fail" and kw["content_type"] == "text/markdown"
    assert out["id"] == "n1"


def test_depot_base64_et_conflit(construits, monkeypatch):
    inst, _ = construits
    inst.upload.return_value = {"id": "n1"}
    _tool("sharepoint_file")(op="upload", drive_id="d1", name="nda.pdf", conflict="rename",
                             content_base64=base64.b64encode(b"%PDF").decode())
    args, kw = inst.upload.call_args
    assert args[2] == b"%PDF" and kw["conflict"] == "rename"
    assert kw["content_type"] == "application/pdf"


@pytest.mark.parametrize("kw", [{}, {"content_text": "a", "content_base64": "YQ=="},
                                {"content_base64": "pas du base64!"}])
def test_depot_contenu_invalide(construits, monkeypatch, kw):
    with pytest.raises(McpError):
        _tool("sharepoint_file")(op="upload", drive_id="d1", name="a.txt", **kw)
    construits[0].upload.assert_not_called()


# --- refus amont ----------------------------------------------------------------

def test_403_refus_nomme_permission(construits, monkeypatch):
    inst, _ = construits
    inst.list_children.side_effect = _upstream(403)
    with pytest.raises(McpError, match="not have rights"):
        _tool("sharepoint_file")(drive_id="d1")


def test_429_reste_reessayable(construits, monkeypatch):
    from oto.tools.common.errors import UpstreamHTTPError

    inst, _ = construits
    inst.list_children.side_effect = _upstream(429)
    with pytest.raises(UpstreamHTTPError):
        _tool("sharepoint_file")(drive_id="d1")


# --- projection -----------------------------------------------------------------

def test_vue_resserree_par_defaut_brut_avec_full(construits, monkeypatch):
    inst, _ = construits
    brut = {"id": "i1", "name": "a.pdf", "file": {"mimeType": "application/pdf"},
            "@microsoft.graph.downloadUrl": "https://x", "createdBy": {"user": {}}}
    inst.list_children.return_value = [brut]
    court = _tool("sharepoint_file")(drive_id="d1")["items"][0]
    assert "@microsoft.graph.downloadUrl" not in court and court["mimeType"] == "application/pdf"
    assert _tool("sharepoint_file")(drive_id="d1", full=True)["items"] == [brut]
    inst.search_sites.return_value = [{"id": "s1", "siteCollection": {"x": 1}}]
    assert "siteCollection" not in _tool("sharepoint_site")(query="a")["sites"][0]
    assert _tool("sharepoint_site")(query="a", full=True)["sites"][0]["siteCollection"]
