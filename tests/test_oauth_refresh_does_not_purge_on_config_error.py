"""Un 4xx de CONFIGURATION ne doit pas détruire un credential valide.

Les trois flux OAuth fédérés purgent le credential quand leur `_refresh` lève
`*ReauthRequired` (`clear_credential` puis `return None`, atlassian_oauth.py ~:196,
folk_oauth.py ~:207). C'est juste quand le GRANT est mort —
l'utilisateur doit re-consentir de toute façon.

Mais jusqu'ici, TOUT 400/401 levait cette exception. Or un serveur d'autorisation
répond 400 aussi pour `invalid_client`, `invalid_request`, `unauthorized_client` :
autrement dit pour une CONFIG fausse. Un client_secret mal saisi effaçait donc
irréversiblement un refresh_token parfaitement valide, et l'utilisateur devait tout
reconnecter — pour une faute de frappe qui n'avait rien détruit.

La distinction vit dans `oauth_flow.grant_is_dead` (une règle, trois appelants).
Ce fichier verrouille la règle ET son application dans les trois modules.
"""
import pytest

from oto_mcp import oauth_flow


# --- la règle -----------------------------------------------------------------

@pytest.mark.parametrize("body", [
    '{"error":"invalid_grant","error_description":"refresh token expired"}',
    '{"error": "invalid_grant"}',
    'error=invalid_grant&error_description=revoked',
])
def test_invalid_grant_is_a_dead_grant(body):
    assert oauth_flow.grant_is_dead(400, body) is True


@pytest.mark.parametrize("body", [
    '{"error":"invalid_client","error_description":"client authentication failed"}',
    '{"error":"unauthorized_client"}',
    '{"error":"invalid_request","error_description":"missing parameter"}',
    '{"error":"invalid_scope"}',
])
def test_config_errors_are_NOT_a_dead_grant(body):
    # Le cas qui coûtait un credential : l'AS dit « ton client est faux », pas
    # « ton grant est mort ». Purger ici, c'est punir l'utilisateur d'une erreur
    # d'administration.
    assert oauth_flow.grant_is_dead(400, body) is False


def test_bare_401_still_counts_but_bare_400_does_not():
    # Certains AS ne renvoient rien sur refresh révoqué : un 401 nu reste un rejet
    # d'identifiants. Un 400 nu est trop ambigu pour justifier une destruction.
    assert oauth_flow.grant_is_dead(401, "") is True
    assert oauth_flow.grant_is_dead(400, "") is False


# --- l'application, module par module -----------------------------------------

class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_post(monkeypatch, mod, resp):
    """Neutralise le réseau ET la résolution de client. ⚠️ `_client_id()` d'atlassian
    et folk déclenche un enregistrement DCR si le client n'est pas connu — un test ne
    doit jamais enregistrer un client OAuth chez un fournisseur."""
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
    if hasattr(mod, "_client_id"):
        monkeypatch.setattr(mod, "_client_id", lambda: "client-de-test")
    if hasattr(mod, "_basic_auth"):
        monkeypatch.setattr(mod, "_basic_auth", lambda: "dGVzdDp0ZXN0")


@pytest.mark.parametrize("modname,excname", [
    ("atlassian_oauth", "AtlassianReauthRequired"),
    ("folk_oauth", "FolkReauthRequired"),
])
def test_dead_grant_still_raises_reauth(monkeypatch, modname, excname):
    import importlib
    mod = importlib.import_module(f"oto_mcp.{modname}")
    exc = getattr(mod, excname)
    _patch_post(monkeypatch, mod, _Resp(400, '{"error":"invalid_grant"}'))
    with pytest.raises(exc):
        mod._refresh("tok")


@pytest.mark.parametrize("modname,excname", [
    ("atlassian_oauth", "AtlassianReauthRequired"),
    ("folk_oauth", "FolkReauthRequired"),
])
def test_config_error_does_NOT_raise_reauth(monkeypatch, modname, excname):
    """TRIPWIRE — le cœur du correctif : sur `invalid_client`, l'exception de réauth
    ne doit PAS être levée, sinon l'appelant purge. Une autre erreur remonte, c'est
    voulu : un incident de config doit se voir."""
    import importlib
    mod = importlib.import_module(f"oto_mcp.{modname}")
    exc = getattr(mod, excname)
    _patch_post(monkeypatch, mod, _Resp(400, '{"error":"invalid_client"}'))
    with pytest.raises(Exception) as e:
        mod._refresh("tok")
    assert not isinstance(e.value, exc), (
        f"{modname}: un invalid_client lève encore la réauth → purge du credential")
