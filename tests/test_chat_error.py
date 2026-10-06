"""Normalisation des erreurs Google Chat (oto-backend#110) : un HttpError brut
devient un message actionnable, jamais un stacktrace."""
from oto_mcp.tools import chat as C


class _Resp:
    def __init__(self, status):
        self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status, message, reason="Error"):
        self.resp = _Resp(status)
        self.content = ('{"error":{"message":%r}}' % message).replace("'", '"').encode()
        self.reason = reason


def _msg(err):
    return _err.error.message if (_err := C._http_error(err)) else ""


def test_chat_app_not_found_404_names_the_oauth_client_project():
    """oto#190 : le 404 vient du projet Google Cloud du client OAuth (aucune app
    Chat configurée), pas du compte — le message ne doit pas envoyer l'utilisateur
    reconnecter un compte qui n'y est pour rien."""
    e = _FakeHttpError(404, "Google Chat app not found. To create a Chat app, you must "
                            "turn on the Chat API and configure the app in the Google "
                            "Cloud console.")
    m = _msg(e)
    assert "Google Cloud project of the OAuth client" in m and "Configuration" in m
    assert "reconnecting the account or retrying changes nothing" in m
    assert "404" not in m                                       # message métier, pas le code brut
    assert "HttpError" not in m and "<" not in m                # pas de repr d'exception


def test_generic_http_error_surfaces_status_and_reason():
    e = _FakeHttpError(403, "Caller does not have permission")
    m = _msg(e)
    assert "HTTP 403" in m and "permission" in m
    assert "HttpError" not in m
