"""Un 4xx de CONFIGURATION ne doit pas détruire un credential valide.

Les flux OAuth lèvent `*ReauthRequired` depuis leur refresh quand — et
SEULEMENT quand — le GRANT est mort (`oauth_flow.grant_is_dead`). L'appelant
(`access_token_for`) réagit à cette exception : jusqu'au 2026-09-04 il PURGEAIT la
ligne du coffre (`clear_credential`) ; depuis oto#25 lot (a), il la MARQUE rejetée
(`update_meta` → `meta.health_ko`/`health_reason`, motif brut) et la laisse en
place — purger rendait « révoqué » indiscernable de « jamais posé ». Ce
comportement du CALLER est verrouillé par `test_google_health_marking.py`, pas ici.

Mais jusqu'ici, TOUT 400/401 levait cette exception. Or un serveur d'autorisation
répond 400 aussi pour `invalid_client`, `invalid_request`, `unauthorized_client` :
autrement dit pour une CONFIG fausse. Un client_secret mal saisi déclenchait donc
la même conséquence qu'un grant mort pour un refresh_token parfaitement valide,
et l'utilisateur devait tout reconnecter — pour une faute de frappe qui n'avait
rien cassé côté fournisseur.

La distinction vit dans `oauth_flow.grant_is_dead` (une règle, ses appelants).
Ce fichier verrouille la règle ET le fait que le refresh lui-même (pas l'appelant,
cf. plus haut) la respecte.

⚠️ **Le tripwire portait sur `atlassian` et `folk` jusqu'au 2026-09-09** ; ils sont
partis avec la fédération MCP (ADR 0069). Il porte désormais sur **google**, le
consommateur vivant de `grant_is_dead` — même règle, même conséquence, et c'est
le connecteur pour lequel une purge à tort ferait le plus de dégâts.
"""
import pytest

from oto_mcp.auth import flow as oauth_flow


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
    """Neutralise le réseau ET la résolution de client — un test ne doit jamais
    joindre un fournisseur ni enregistrer un client OAuth chez lui."""
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
    if hasattr(mod, "_client_id"):
        monkeypatch.setattr(mod, "_client_id", lambda: "client-de-test")
    if hasattr(mod, "_client_secret"):
        monkeypatch.setattr(mod, "_client_secret", lambda: "secret-de-test")
    if hasattr(mod, "_basic_auth"):
        monkeypatch.setattr(mod, "_basic_auth", lambda: "dGVzdDp0ZXN0")


def test_dead_grant_still_raises_reauth(monkeypatch):
    from oto_mcp.auth import google as mod
    _patch_post(monkeypatch, mod, _Resp(400, '{"error":"invalid_grant"}'))
    with pytest.raises(mod.GoogleReauthRequired):
        mod._refresh_access_token("tok", "sub-1")


def test_config_error_does_NOT_raise_reauth(monkeypatch):
    """TRIPWIRE — le cœur du correctif : sur `invalid_client`, l'exception de réauth
    ne doit PAS être levée, sinon l'appelant purge. Une autre erreur remonte, c'est
    voulu : un incident de config doit se voir."""
    from oto_mcp.auth import google as mod
    _patch_post(monkeypatch, mod, _Resp(400, '{"error":"invalid_client"}'))
    with pytest.raises(Exception) as e:
        mod._refresh_access_token("tok", "sub-1")
    assert not isinstance(e.value, mod.GoogleReauthRequired), (
        "google : un invalid_client lève encore la réauth → purge du credential")
