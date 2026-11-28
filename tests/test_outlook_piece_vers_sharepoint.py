"""Une pièce jointe Outlook comme SOURCE d'un dépôt (signal #1486 : une équipe RH classe
chaque jour des pièces reçues par mail dans SharePoint).

Ce que ce fichier verrouille :
- `file_source.resolve({"kind": "outlook", …})` : la pièce désignée comme
  `outlook(op="attachment")` la désigne (`message_id` + `attachment_id` OU `filename`),
  lue par le même lecteur (`_microsoft_graph.attachment`), nom et type venus de Graph ;
- la taille LISTÉE par Graph est jugée AVANT de lire le contenu : une pièce trop grosse
  est refusée sans que son contenu soit demandé ;
- une pièce introuvable (nom ou id) est refusée en la nommant, avec la liste des pièces ;
- le compte : celui de l'appel par défaut (jeton demandé sans compte), `account` dans la
  source en désigne un autre ;
- `sharepoint_file(op="upload", source={"kind": "outlook", …})` dépose les octets lus
  chez Graph, sans qu'ils passent par l'agent ; un `name` de l'appel l'emporte.

Le cœur Microsoft est MOQUÉ à sa frontière (`oto.tools.microsoft` posé dans
`sys.modules`) : aucun appel à Graph.
"""
import asyncio
import base64
import sys
import types
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError

PIECES = [{"id": "a1", "name": "arret.pdf", "contentType": "application/pdf", "size": 4},
          {"id": "a2", "name": "photo.jpg", "contentType": "image/jpeg", "size": 900}]


@pytest.fixture
def graph(monkeypatch):
    """Faux `MailClient` et `FilesClient`, et les jetons demandés `(service, compte)`."""
    mail, files, jetons = MagicMock(name="MailClient"), MagicMock(name="FilesClient"), []
    mail.list_attachments.return_value = PIECES
    mail.get_attachment.side_effect = lambda mid, aid: {
        **next(p for p in PIECES if p["id"] == aid),
        "contentBytes": base64.b64encode(b"%PDF").decode()}
    files.get_my_drive.return_value = {"id": "d-moi"}
    files.upload.return_value = {"id": "n1", "name": "déposé"}

    coeur = types.ModuleType("oto.tools.microsoft")
    coeur.MailClient = lambda jeton: mail
    coeur.FilesClient = lambda jeton: files
    coeur.WorkbookClient = MagicMock(name="WorkbookClient")

    class MicrosoftAuthError(ValueError):
        status_code = 401

    coeur.MicrosoftAuthError = MicrosoftAuthError
    monkeypatch.setitem(sys.modules, "oto.tools.microsoft", coeur)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service, account=None):
        jetons.append((service, account))
        return f"AT-{service}"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    mail.jetons = jetons
    return mail, files


def _resolve(source, **kw):
    from oto_mcp import file_source
    return file_source.resolve(source, **kw)


# --- la source `outlook` -------------------------------------------------------

def test_source_outlook_par_id_nom_et_type_de_graph(graph):
    mail, _ = graph
    rf = _resolve({"kind": "outlook", "message_id": "m1", "attachment_id": "a1"})
    assert (rf.data, rf.filename, rf.mime) == (b"%PDF", "arret.pdf", "application/pdf")
    mail.get_attachment.assert_called_once_with("m1", "a1")
    # Le compte de l'appel : le jeton se demande pour `outlook`, sans compte nommé.
    assert mail.jetons == [("outlook", None)]


def test_source_outlook_par_nom(graph):
    mail, _ = graph
    rf = _resolve({"kind": "outlook", "message_id": "m1", "filename": "arret.pdf"})
    assert rf.filename == "arret.pdf"
    mail.get_attachment.assert_called_once_with("m1", "a1")


def test_source_outlook_compte_designe(graph):
    mail, _ = graph
    _resolve({"kind": "outlook", "message_id": "m1", "attachment_id": "a1",
              "account": "rh@contoso.example"})
    assert mail.jetons == [("outlook", "rh@contoso.example")]


@pytest.mark.parametrize("source, motif", [
    ({"filename": "absent.pdf"}, "0 attachments are named 'absent.pdf'"),
    ({"attachment_id": "zz"}, "no attachment with id 'zz'"),
])
def test_source_outlook_piece_introuvable_refus_nomme(graph, source, motif):
    from oto_mcp.file_source import FileSourceError

    mail, _ = graph
    with pytest.raises(FileSourceError, match=motif) as e:
        _resolve({"kind": "outlook", "message_id": "m1", **source})
    assert "source outlook" in str(e.value) and "arret.pdf" in str(e.value)
    mail.get_attachment.assert_not_called()


def test_source_outlook_trop_grosse_refusee_avant_lecture(graph):
    from oto_mcp.file_source import FileSourceError

    mail, _ = graph
    with pytest.raises(FileSourceError, match="photo.jpg.*900 bytes.*over the 100"):
        _resolve({"kind": "outlook", "message_id": "m1", "attachment_id": "a2"},
                 max_bytes=100)
    mail.get_attachment.assert_not_called()


@pytest.mark.parametrize("source", [
    {"message_id": "m1"},
    {"attachment_id": "a1"},
    {"message_id": "m1", "attachment_id": "a1", "filename": "arret.pdf"},
    {"message_id": "m1", "attachment_id": "a1", "account": ""},
])
def test_source_outlook_mal_formee(graph, source):
    from oto_mcp.file_source import FileSourceError

    with pytest.raises(FileSourceError, match="source outlook"):
        _resolve({"kind": "outlook", **source})
    assert graph[0].jetons == []


def test_source_outlook_message_ou_courriel_joint_refuse(graph):
    from oto_mcp.file_source import FileSourceError

    mail, _ = graph
    mail.get_attachment.side_effect = lambda mid, aid: {
        "id": aid, "name": "Fwd", "@odata.type": "#microsoft.graph.itemAttachment"}
    with pytest.raises(FileSourceError, match="not a file attachment"):
        _resolve({"kind": "outlook", "message_id": "m1", "attachment_id": "a1"})


# --- le dépôt SharePoint depuis une pièce jointe --------------------------------

def _sharepoint_file():
    from fastmcp import FastMCP
    from oto_mcp.tools import sharepoint as X

    m = FastMCP("t")
    X.register(m)
    return asyncio.run(m.get_tool("sharepoint_file")).fn


def test_depot_sharepoint_depuis_une_piece_jointe(graph):
    mail, files = graph
    _sharepoint_file()(op="upload", drive_id="d1", path="RH/Arrêts",
                       source={"kind": "outlook", "message_id": "m1",
                               "attachment_id": "a1"})
    args, kw = files.upload.call_args
    assert args == ("d1", "arret.pdf", b"%PDF")
    assert kw["content_type"] == "application/pdf" and kw["parent_path"] == "RH/Arrêts"
    assert kw["conflict"] == "fail"
    # Un compte pour les deux côtés : celui de l'appel, service par service.
    assert ("sharepoint", None) in mail.jetons and ("outlook", None) in mail.jetons


def test_depot_sharepoint_nom_de_l_appel_l_emporte(graph):
    _, files = graph
    _sharepoint_file()(op="upload", drive_id="d1", name="2026-11-arret-dupont.pdf",
                       source={"kind": "outlook", "message_id": "m1",
                               "filename": "arret.pdf"})
    assert files.upload.call_args.args[1] == "2026-11-arret-dupont.pdf"


def test_depot_sharepoint_piece_introuvable_rien_n_est_depose(graph):
    _, files = graph
    with pytest.raises(McpError, match="source.*no attachment with id 'zz'"):
        _sharepoint_file()(op="upload", drive_id="d1",
                           source={"kind": "outlook", "message_id": "m1",
                                   "attachment_id": "zz"})
    files.upload.assert_not_called()


def test_depot_sharepoint_piece_trop_grosse_refusee_avant_lecture(graph):
    from oto_mcp.tools import sharepoint as X

    mail, files = graph
    mail.list_attachments.return_value = [{**PIECES[0], "size": X._UPLOAD_MAX + 1}]
    with pytest.raises(McpError, match="source.*over the"):
        _sharepoint_file()(op="upload", drive_id="d1",
                           source={"kind": "outlook", "message_id": "m1",
                                   "attachment_id": "a1"})
    mail.get_attachment.assert_not_called()
    files.upload.assert_not_called()
