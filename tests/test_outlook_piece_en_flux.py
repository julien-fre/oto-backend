"""Une pièce jointe Outlook lue EN FLUX (oto-core v1.255.0), et la session gardée des
clients Microsoft.

Ce que ce fichier verrouille, avec la VRAIE lib (`MailClient`) branchée sur une fausse
session HTTP — aucun appel à Graph :
- `_microsoft_graph.attachment` lit par `get_attachment_content`, borné au plafond de
  l'appelant : une pièce dont la taille déclarée dépasse le plafond est refusée
  d'avance, son contenu jamais demandé (`$value`) ; un contenu plus long que déclaré
  est coupé en lecture ; une pièce qui n'est pas un fichier est refusée sans lecture ;
  les deux refus de la lib deviennent des refus nommés ;
- le jeton part avec chaque requête, jamais posé sur la session ;
- chaque client Microsoft construit par le backend reçoit la session gardée
  d'`auth/microsoft.graph_session` (`egress_transport`) : par `client_for`, ou à la
  construction (`session=`), ou — `OneNoteClient`, dont le constructeur ne la prend
  pas — par affectation.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from oto_mcp.mcp_errors import McpError

pytestmark = pytest.mark.exige_pin_oto_core

FICHIER = "#microsoft.graph.fileAttachment"
RACINE = Path(__file__).resolve().parents[1] / "oto_mcp"


class _Reponse:
    """Une réponse `requests` lue en flux : ce que la lib consomme, et pas plus."""

    def __init__(self, status=200, corps=b"", morceaux=None, entetes=None):
        self.status_code = status
        self.headers = dict(entetes or {})
        self._corps = corps
        self._morceaux = list(morceaux) if morceaux is not None else [corps]
        self.lus = 0
        self.fermee = False

    def json(self):
        return json.loads(self._corps)

    def iter_content(self, taille=1):
        for morceau in self._morceaux:
            self.lus += 1
            yield morceau

    def close(self):
        self.fermee = True


class _Session:
    """La fausse session : les requêtes reçues, une réponse par chemin."""

    def __init__(self, reponses):
        self.headers = {}
        self.appels = []
        self._reponses = reponses

    def request(self, method, url, **kw):
        self.appels.append((method, url, kw))
        for suffixe, reponse in self._reponses.items():
            if url.split("?")[0].endswith(suffixe):
                return reponse
        raise AssertionError(f"requête inattendue : {method} {url}")


def _meta(**champs):
    return _Reponse(corps=json.dumps({"@odata.type": FICHIER, "name": "scan.pdf",
                                      "contentType": "application/pdf", **champs}).encode())


def _client(reponses):
    from oto.tools.microsoft import MailClient

    session = _Session(reponses)
    return MailClient("AT-secret", session=session), session


def _lire(client, **kw):
    from oto_mcp.tools import _microsoft_graph as graph

    return graph.attachment(client, "m1", attachment_id="a1", **kw)


def _valeurs(session):
    return [url for _, url, _ in session.appels if url.endswith("/$value")]


def test_lue_en_flux_par_la_session_le_jeton_a_chaque_requete():
    valeur = _Reponse(morceaux=[b"%PD", b"F-1"], entetes={"Content-Type": "application/pdf"})
    client, session = _client({"/attachments/a1": _meta(size=6), "/$value": valeur})
    piece, octets = _lire(client, max_bytes=100)
    assert octets == b"%PDF-1"
    assert piece == {"id": "a1", "name": "scan.pdf", "contentType": "application/pdf"}
    assert len(session.appels) == 2 and _valeurs(session)
    assert session.appels[1][2]["stream"] is True
    # Le jeton voyage avec CHAQUE requête ; la session n'en porte aucun.
    assert all(kw["headers"]["Authorization"] == "Bearer AT-secret"
               for _, _, kw in session.appels)
    assert "Authorization" not in session.headers
    assert valeur.fermee


def test_trop_grosse_refusee_d_avance_sans_demander_le_contenu():
    client, session = _client({"/attachments/a1": _meta(size=900),
                               "/$value": _Reponse(corps=b"x" * 900)})
    with pytest.raises(McpError, match="« scan.pdf » weighs 900 bytes, over the 100 "
                                       "bytes this gesture carries. Nothing was done."):
        _lire(client, max_bytes=100)
    assert _valeurs(session) == [], "le contenu n'est jamais demandé"


def test_plus_longue_que_declaree_coupee_en_lecture():
    valeur = _Reponse(morceaux=[b"x" * 60, b"x" * 60, b"x" * 60, b"x" * 60])
    client, _ = _client({"/attachments/a1": _meta(size=10), "/$value": valeur})
    with pytest.raises(McpError, match="« scan.pdf » goes beyond the 100 bytes this "
                                       "gesture carries. Nothing was done."):
        _lire(client, max_bytes=100)
    assert valeur.lus == 2, "la lecture s'arrête au premier morceau qui dépasse"
    assert valeur.fermee


@pytest.mark.parametrize("genre", ["#microsoft.graph.itemAttachment",
                                   "#microsoft.graph.referenceAttachment"])
def test_piece_non_fichier_refusee_sans_lecture(genre):
    client, session = _client({"/attachments/a1": _meta(**{"@odata.type": genre}),
                               "/$value": _Reponse(corps=b"x")})
    with pytest.raises(McpError, match=f"« a1 » is not a file attachment \\({genre}\\)"):
        _lire(client, max_bytes=100)
    assert _valeurs(session) == []


def test_id_inconnu_nomme_avec_la_liste_des_pieces():
    from oto.tools.microsoft import MailClient

    introuvable = _Reponse(status=404, corps=json.dumps(
        {"error": {"code": "ErrorItemNotFound", "message": "not found"}}).encode())
    client = MailClient("AT", session=_Session({"/attachments/a1": introuvable}))
    client.list_attachments = lambda mid: [{"id": "a2", "name": "autre.pdf"}]
    with pytest.raises(McpError, match=r"no attachment with id 'a1' on this message .*"
                                       r"autre\.pdf"):
        _lire(client, max_bytes=100)


# --- la session gardée des clients Microsoft -------------------------------------

def _gardee(s) -> bool:
    from oto_mcp.egress_transport import AdaptateurEpingle

    return all(isinstance(s.get_adapter(f"{schema}://graph.microsoft.com/"),
                          AdaptateurEpingle)
               for schema in ("http", "https")) and s.trust_env is False


def test_graph_session_est_gardee_sans_exception_d_egress():
    from oto_mcp import egress
    from oto_mcp.auth import microsoft as ms_auth

    s = ms_auth.graph_session("outlook")
    assert _gardee(s)
    politique = s.get_adapter("https://graph.microsoft.com/")._politique
    assert (politique.connector, politique.titulaire) == ("outlook", egress.AUCUN)


def test_client_for_passe_le_jeton_et_la_session_gardee(monkeypatch):
    from oto_mcp.tools import _microsoft_graph as graph

    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for",
                        lambda sub, service, *a, **k: f"AT-{service}")
    construits = []

    def classe(jeton, *, session, **kw):
        construits.append((jeton, session, kw))
        return "client"

    assert graph.client_for(classe, "teams", renew=True, racine="x") == "client"
    ((jeton, session, kw),) = construits
    assert jeton == "AT-teams" and kw == {"racine": "x"} and _gardee(session)


def test_un_vrai_client_graph_garde_la_session_recue():
    from oto.tools.microsoft import (CalendarClient, DirectoryClient, FilesClient,
                                     MailClient, PowerBIClient, TeamsClient,
                                     WorkbookClient)
    from oto_mcp.auth import microsoft as ms_auth

    for classe in (CalendarClient, DirectoryClient, FilesClient, MailClient,
                   PowerBIClient, TeamsClient, WorkbookClient):
        s = ms_auth.graph_session("microsoft")
        c = classe("AT", session=s)
        assert c.session is s and "Authorization" not in s.headers, classe.__name__


def _classes_microsoft() -> set[str]:
    import oto.tools.microsoft as coeur

    return {n for n in coeur.__all__ if n.endswith("Client")}


def _sites():
    """(fichier, classe, mots-clés) de chaque construction directe d'un client
    Microsoft, et les classes passées à `client_for`. Un module qui ne nomme pas
    `oto.tools.microsoft` n'en construit aucun (`tools/calendar.py` : Google)."""
    classes = _classes_microsoft()
    directs, par_client_for = [], set()
    for f in RACINE.rglob("*.py"):
        source = f.read_text()
        if "oto.tools.microsoft" not in source and "client_for" not in source:
            continue
        for n in ast.walk(ast.parse(source)):
            if not isinstance(n, ast.Call):
                continue
            nom = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(
                n.func, "id", None)
            if nom in classes and "oto.tools.microsoft" in source:
                directs.append((f.relative_to(RACINE).as_posix(), nom,
                                {k.arg for k in n.keywords}))
            elif nom == "client_for" and n.args and isinstance(n.args[0], ast.Name):
                par_client_for.add(n.args[0].id)
    return directs, par_client_for


def test_chaque_client_microsoft_sort_par_la_session_gardee():
    directs, par_client_for = _sites()
    assert par_client_for >= {"MailClient", "FilesClient", "WorkbookClient",
                              "CalendarClient", "TeamsClient", "DirectoryClient",
                              "PowerBIClient"}, par_client_for
    sans_session = [(f, c) for f, c, kws in directs if "session" not in kws]
    # Seul `OneNoteClient` (constructeur sans `session=`) : la session s'affecte.
    assert sans_session == [("tools/onenote.py", "OneNoteClient")], sans_session
    source = (RACINE / "tools/onenote.py").read_text()
    assert "c.session = ms_auth.graph_session(_SERVICE)" in source
