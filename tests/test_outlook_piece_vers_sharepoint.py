"""Une pièce jointe Outlook comme SOURCE d'un dépôt (signal #1486 : une équipe RH classe
chaque jour des pièces reçues par mail dans SharePoint).

Ce que ce fichier verrouille :
- `file_source.resolve({"kind": "outlook", …})` : la pièce désignée comme
  `outlook(op="attachment")` la désigne (`message_id` + `attachment_id` OU `filename`),
  lue par le même lecteur (`_microsoft_graph.attachment`), nom et type venus de Graph ;
- le contenu se lit par `MailClient.get_attachment_content`, borné au plafond de
  l'appelant ; ses deux refus (trop grosse, pas un fichier) deviennent des refus nommés
  (la lecture en flux elle-même : `test_outlook_piece_en_flux.py`) ;
- une pièce introuvable (nom ou id) est refusée en la nommant, avec la liste des pièces ;
- le compte : celui de l'appel par défaut (jeton demandé sans compte), `account` dans la
  source en désigne un autre ;
- `sharepoint_file(op="upload", source={"kind": "outlook", …})` dépose les octets lus
  chez Graph, sans qu'ils passent par l'agent ; un `name` de l'appel l'emporte.

Le cœur Microsoft est MOQUÉ à sa frontière (`oto.tools.microsoft` posé dans
`sys.modules`) : aucun appel à Graph.
"""
import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest
from oto_mcp.file_source import DEFAULT_MAX_BYTES
from oto_mcp.mcp_errors import McpError

PIECES = [{"id": "a1", "name": "arret.pdf", "contentType": "application/pdf", "size": 4},
          {"id": "a2", "name": "photo.jpg", "contentType": "image/jpeg", "size": 900}]


@pytest.fixture
def graph(monkeypatch):
    """Faux `MailClient` et `FilesClient`, et les jetons demandés `(service, compte)`."""
    import oto.tools.microsoft as vrai
    from oto.tools.common.errors import UpstreamHTTPError

    mail, files, jetons = MagicMock(name="MailClient"), MagicMock(name="FilesClient"), []
    mail.list_attachments.return_value = PIECES

    def contenu(mid, aid, *, max_bytes):
        # Ce que fait la lib : la taille déclarée jugée avant le contenu, un id inconnu
        # refusé par Graph (404). Les pièces sont celles que liste le message.
        piece = next((p for p in mail.list_attachments.return_value if p["id"] == aid),
                     None)
        if piece is None:
            raise UpstreamHTTPError(404, {"error": {"message": "not found"}},
                                    service="microsoft")
        if piece["size"] > max_bytes:
            raise vrai.AttachmentTooLarge(piece["name"], max_bytes, size=piece["size"])
        return vrai.AttachmentContent(name=piece["name"], content_type=piece["contentType"],
                                      content=b"%PDF")

    mail.get_attachment_content.side_effect = contenu
    files.get_my_drive.return_value = {"id": "d-moi"}
    files.upload.return_value = {"id": "n1", "name": "déposé"}

    coeur = types.ModuleType("oto.tools.microsoft")
    coeur.MailClient = lambda jeton, *, session: mail
    coeur.FilesClient = lambda jeton, *, session: files
    coeur.WorkbookClient = MagicMock(name="WorkbookClient")
    coeur.AttachmentContent = vrai.AttachmentContent
    coeur.AttachmentTooLarge = vrai.AttachmentTooLarge
    coeur.UnsupportedAttachment = vrai.UnsupportedAttachment

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
    # Lue d'emblée par son id, bornée au plafond de l'appelant : aucune liste.
    mail.get_attachment_content.assert_called_once_with("m1", "a1",
                                                        max_bytes=DEFAULT_MAX_BYTES)
    mail.list_attachments.assert_not_called()
    # Le compte de l'appel : le jeton se demande pour `outlook`, sans compte nommé.
    assert mail.jetons == [("outlook", None)]


def test_source_outlook_par_nom(graph):
    mail, _ = graph
    rf = _resolve({"kind": "outlook", "message_id": "m1", "filename": "arret.pdf"})
    assert rf.filename == "arret.pdf"
    mail.get_attachment_content.assert_called_once_with("m1", "a1",
                                                        max_bytes=DEFAULT_MAX_BYTES)


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


def test_source_outlook_trop_grosse_refusee_avant_lecture(graph):
    from oto_mcp.file_source import FileSourceError

    mail, _ = graph
    with pytest.raises(FileSourceError, match="photo.jpg.*900 bytes.*over the 100"):
        _resolve({"kind": "outlook", "message_id": "m1", "attachment_id": "a2"},
                 max_bytes=100)
    # Le plafond de l'appelant est celui que reçoit la lib.
    assert mail.get_attachment_content.call_args.kwargs == {"max_bytes": 100}


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

    from oto.tools.microsoft import UnsupportedAttachment

    mail, _ = graph
    mail.get_attachment_content.side_effect = UnsupportedAttachment(
        "a1", "#microsoft.graph.itemAttachment")
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
    assert mail.get_attachment_content.call_args.kwargs == {"max_bytes": X._UPLOAD_MAX}
    files.upload.assert_not_called()
