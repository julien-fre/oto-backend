"""Tools `outlook_*` du connecteur Outlook (service du porteur `microsoft`).

Ce que ce fichier verrouille :
- la SURFACE (2 tools) et le jeton : celui de l'appelant pour le service `outlook`
  (`auth/microsoft.access_token_for`), seul passé au client ; un compte qui n'a pas
  autorisé Outlook → le refus nommé du porteur, avant de construire le client ;
- le dispatch des ops de `outlook_message` vers les VRAIES méthodes de `MailClient`
  (client `create_autospec` sur la classe de la lib : une signature fausse casse ici) ;
- la vue resserrée par défaut, le brut avec `full=True`, `fields` ;
- les déplacements (archive, trash, move) : nouvel id rendu, échec partiel nommé ;
- `outlook_compose` : BROUILLON par défaut, envoi explicite = brouillon puis
  `send_draft`, `kind` qui dit l'acte, réponse sans destinataires ni pièces, pièces
  jointes résolues en octets et bornées à 3 Mo avant toute écriture.
"""
import asyncio
import base64
from unittest.mock import create_autospec

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    """Le faux `MailClient` (autospec de la vraie classe) et les jetons reçus."""
    import oto.tools.microsoft as coeur

    inst = create_autospec(coeur.MailClient, instance=True)
    jetons = []

    def fabrique(jeton):
        jetons.append(jeton)
        return inst

    monkeypatch.setattr(coeur, "MailClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service):
        assert service == "outlook", "le jeton se demande pour CE service"
        return f"AT-{sub}"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    inst.jetons = jetons
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import outlook as X

    m = FastMCP("t")
    X.register(m)
    return m


def _tool(name):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, {"error": {"message": "nope"}}, service="microsoft")


MSG = {"id": "m1", "subject": "Facture", "bodyPreview": "Bonjour",
       "from": {"emailAddress": {"name": "Jane", "address": "jane@contoso.example"}},
       "toRecipients": [{"emailAddress": {"address": "moi@contoso.example"}}],
       "isRead": False, "hasAttachments": True, "receivedDateTime": "2026-10-08T09:00:00Z",
       "internetMessageHeaders": ["lourd"]}


# --- surface & jeton ------------------------------------------------------------

def test_surface(client):
    assert {t.name for t in asyncio.run(_mcp().list_tools())} == {
        "outlook_message", "outlook_compose"}


def test_le_client_porte_le_jeton_de_l_appelant(client):
    client.search_messages.return_value = []
    _tool("outlook_message")()
    assert client.jetons == ["AT-sub-1"]


def test_un_compte_sans_outlook_recoit_le_refus_du_porteur(client, monkeypatch):
    def refus(sub, service):
        raise RuntimeError("The Microsoft account jane@contoso.example has not yet "
                           "authorized Outlook: connect Outlook from its card")

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", refus)
    with pytest.raises(McpError, match="has not yet authorized Outlook"):
        _tool("outlook_message")()
    assert client.jetons == []


def test_op_inconnue_refusee_avant_le_jeton(client):
    with pytest.raises(McpError, match="op must be"):
        _tool("outlook_message")(op="delete")
    assert client.jetons == []


# --- lecture --------------------------------------------------------------------

def test_search_vue_resserree_brut_et_fields(client):
    client.search_messages.return_value = [dict(MSG)]
    out = _tool("outlook_message")(query="from:jane", folder="inbox", limit=5)
    client.search_messages.assert_called_once_with("from:jane", folder="inbox", top=5,
                                                   unread=None)
    m = out["messages"][0]
    assert m["from"] == "Jane <jane@contoso.example>" and m["preview"] == "Bonjour"
    assert "internetMessageHeaders" not in m and out["count"] == 1
    assert _tool("outlook_message")(full=True)["messages"] == [MSG]
    assert _tool("outlook_message")(fields=["id", "subject"])["messages"] == [
        {"id": "m1", "subject": "Facture"}]


def test_query_et_unread_s_excluent_avec_le_message_de_la_lib(client):
    # La VRAIE règle de la lib, rejouée sur un client réel : `$search` et `$filter`.
    from oto.tools.microsoft.mail import MailClient

    client.search_messages.side_effect = lambda *a, **k: MailClient.search_messages(
        MailClient("AT"), *a, **k)
    with pytest.raises(McpError, match="unread cannot be combined with query"):
        _tool("outlook_message")(query="facture", unread=True)


def test_drafts_et_folders(client):
    client.search_messages.return_value = []
    _tool("outlook_message")(op="drafts", limit=3)
    client.search_messages.assert_called_once_with(folder="drafts", top=3)
    client.list_folders.return_value = [{"id": "f1", "displayName": "Inbox",
                                         "unreadItemCount": 2, "x": 1}]
    out = _tool("outlook_message")(op="folders")
    assert out["folders"] == [{"id": "f1", "displayName": "Inbox", "totalItemCount": None,
                               "unreadItemCount": 2, "childFolderCount": None}]


def test_get_rend_le_corps_et_les_pieces(client):
    client.get_message.return_value = {**MSG, "body": {"content": "Bonjour Jane"}}
    client.list_attachments.return_value = [
        {"id": "a1", "name": "f.pdf", "contentType": "application/pdf", "size": 9,
         "lastModifiedDateTime": "x"}]
    out = _tool("outlook_message")(op="get", message_id="m1")
    client.get_message.assert_called_once_with("m1")
    assert out["body"] == "Bonjour Jane"
    assert out["attachments"] == [{"id": "a1", "name": "f.pdf",
                                   "contentType": "application/pdf", "size": 9,
                                   "isInline": None}]


def test_piece_jointe_par_nom_rendue_pour_l_agent(client, monkeypatch):
    client.list_attachments.return_value = [{"id": "a1", "name": "f.pdf"}]
    client.get_attachment.return_value = {
        "name": "f.pdf", "contentType": "application/pdf",
        "contentBytes": base64.b64encode(b"%PDF").decode()}
    vus = []
    monkeypatch.setattr("oto_mcp.file_content.render_for_agent",
                        lambda data, nom, mime, **kw: vus.append((data, nom, mime, kw))
                        or {"encoding": "text"})
    _tool("outlook_message")(op="attachment", message_id="m1", filename="f.pdf")
    client.get_attachment.assert_called_once_with("m1", "a1")
    data, nom, mime, kw = vus[0]
    assert (data, nom, mime) == (b"%PDF", "f.pdf", "application/pdf")
    assert kw["prefix"] == "outlook-attachments" and kw["sub"] == "sub-1"


def test_piece_jointe_ambigue_ou_non_fichier_refusee(client):
    client.list_attachments.return_value = [{"id": "a1", "name": "x"}, {"id": "a2",
                                                                        "name": "x"}]
    with pytest.raises(McpError, match="attachment_id"):
        _tool("outlook_message")(op="attachment", message_id="m1", filename="x")
    client.get_attachment.return_value = {"name": "Fwd", "@odata.type":
                                          "#microsoft.graph.itemAttachment"}
    with pytest.raises(McpError, match="not a file attachment"):
        _tool("outlook_message")(op="attachment", message_id="m1", attachment_id="a1")


def test_argument_d_une_autre_op_refuse(client):
    with pytest.raises(McpError, match="does not use `destination`"):
        _tool("outlook_message")(destination="archive")
    with pytest.raises(McpError, match="op='attachment'"):
        _tool("outlook_message")(sheet=0)


# --- déplacements ---------------------------------------------------------------

@pytest.mark.parametrize("op, cible", [("archive", "archive"), ("trash", "deleteditems")])
def test_archive_et_trash_deplacent_et_rendent_le_nouvel_id(client, op, cible):
    client.move.side_effect = lambda mid, dest: {"id": f"nouveau-{mid}"}
    out = _tool("outlook_message")(op=op, message_ids=["m1", "m2"])
    assert [c.args for c in client.move.call_args_list] == [("m1", cible), ("m2", cible)]
    assert out == {"destination": cible, "moved": [
        {"id": "m1", "new_id": "nouveau-m1"}, {"id": "m2", "new_id": "nouveau-m2"}]}
    client.delete.assert_not_called()


def test_move_exige_sa_destination_et_un_echec_partiel_se_nomme(client):
    with pytest.raises(McpError, match="requires `destination`"):
        _tool("outlook_message")(op="move", message_ids=["m1"])
    with pytest.raises(McpError, match="requires `message_ids`"):
        _tool("outlook_message")(op="move", message_ids=[], destination="f1")
    client.move.side_effect = [{"id": "n1"}, _upstream(404), {"id": "n3"}]
    with pytest.raises(McpError) as e:
        _tool("outlook_message")(op="move", message_ids=["m1", "m2", "m3"],
                                 destination="f1")
    assert "PARTIAL" in str(e.value) and "'n1'" in str(e.value) and "['m3']" in str(e.value)
    assert client.move.call_count == 2


def test_un_403_se_dit_en_droits_un_429_reste_reessayable(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.search_messages.side_effect = _upstream(403)
    with pytest.raises(McpError, match="not have rights"):
        _tool("outlook_message")()
    client.search_messages.side_effect = _upstream(429)
    with pytest.raises(UpstreamHTTPError):
        _tool("outlook_message")()


# --- composer -------------------------------------------------------------------

def test_brouillon_par_defaut_markdown_en_html(client):
    client.create_draft.return_value = {"id": "d1", "subject": "Point",
                                        "toRecipients": [{"emailAddress": {
                                            "address": "marc@fabrikam.example"}}]}
    out = _tool("outlook_compose")(body="**Bonjour**", to=["marc@fabrikam.example"],
                                   subject="Point", cc=["a@b.example"])
    kw = client.create_draft.call_args.kwargs
    assert kw["to"] == ["marc@fabrikam.example"] and kw["cc"] == ["a@b.example"]
    assert "<strong>Bonjour</strong>" in kw["body_html"] and kw["attachments"] == []
    client.send_draft.assert_not_called()
    assert out["kind"] == "draft" and out["id"] == "d1"
    assert out["to"] == ["marc@fabrikam.example"]


def test_send_explicite_passe_par_le_brouillon(client):
    client.create_draft.return_value = {"id": "d1"}
    client.send_draft.return_value = "d1"
    out = _tool("outlook_compose")(body="x", to=["marc@fabrikam.example"], mode="send")
    client.create_draft.assert_called_once()
    client.send_draft.assert_called_once_with("d1")
    assert out["kind"] == "sent" and out["draft_id"] == "d1"


def test_envoi_echoue_le_brouillon_reste_et_c_est_dit(client):
    client.create_draft.return_value = {"id": "d1"}
    client.send_draft.side_effect = _upstream(400)
    with pytest.raises(McpError, match="d1 was SAVED"):
        _tool("outlook_compose")(body="x", to=["m@f.example"], mode="send")


def test_reponse_sans_destinataires_ni_pieces(client):
    client.create_reply_draft.return_value = {"id": "r1"}
    out = _tool("outlook_compose")(body="Merci", reply_to="m1", reply_all=True)
    args, kw = client.create_reply_draft.call_args
    assert args == ("m1",) and kw["reply_all"] is True and "Merci" in kw["body_html"]
    assert out["kind"] == "draft"
    for extra in ({"to": ["a@b.example"]}, {"subject": "x"},
                  {"attachments": [{"kind": "url", "url": "https://x"}]}):
        with pytest.raises(McpError, match="not accepted on a reply"):
            _tool("outlook_compose")(body="x", reply_to="m1", **extra)
    with pytest.raises(McpError, match="`to` is required"):
        _tool("outlook_compose")(body="x")
    with pytest.raises(McpError, match="reply_all"):
        _tool("outlook_compose")(body="x", to=["a@b.example"], reply_all=True)


def test_pieces_jointes_en_octets_et_bornees_avant_ecriture(client, monkeypatch):
    from oto_mcp import file_source
    from oto.tools.microsoft.mail import MAX_INLINE_ATTACHMENT

    plafonds = []

    def resolve(src, max_bytes):
        plafonds.append(max_bytes)
        if src["url"].endswith("gros"):
            raise file_source.FileSourceError("fichier de 9999999 octets > plafond")
        return file_source.ResolvedFile(b"%PDF", "../../contrat.pdf", "application/pdf")

    monkeypatch.setattr(file_source, "resolve", resolve)
    client.create_draft.return_value = {"id": "d1"}
    _tool("outlook_compose")(body="x", to=["m@f.example"],
                             attachments=[{"kind": "url", "url": "https://a/ok"}])
    assert client.create_draft.call_args.kwargs["attachments"] == [
        {"name": "contrat.pdf", "content_type": "application/pdf",
         "content_bytes": b"%PDF"}]
    assert plafonds == [MAX_INLINE_ATTACHMENT]
    client.create_draft.reset_mock()
    with pytest.raises(McpError, match="up to 3 MB"):
        _tool("outlook_compose")(body="x", to=["m@f.example"],
                                 attachments=[{"kind": "url", "url": "https://a/gros"}])
    client.create_draft.assert_not_called()


def test_la_description_dit_brouillon_par_defaut_et_pas_de_signature():
    import oto.tools.microsoft  # noqa: F401 — le vrai cœur, pour l'enregistrement

    desc = asyncio.run(_mcp().get_tool("outlook_compose")).description
    assert "DRAFT by default" in desc and "No signature" in desc
