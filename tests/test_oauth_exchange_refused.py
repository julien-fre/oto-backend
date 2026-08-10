"""Un refus du serveur d'autorisation n'est pas un bug backend.

L'échange `authorization_code` échoue pour des raisons qui décrivent toutes la
Connected App ou le grant de l'UTILISATEUR : code expiré ou déjà consommé,
verifier PKCE qui ne correspond pas, client_id/secret faux, scopes absents,
callback URL divergente, restriction IP. Rien de tout cela n'est actionnable
côté serveur — mais ça partait quand même dans Sentry, où trois events de refus
Salesforce trônaient en tête du tableau du 31/07.

Ce que ces tests verrouillent, c'est la SÉPARATION : `OAuthExchangeRefused` est
droppée, `OAuthFlowError` nue ne l'est pas. Sans elle, un `OTO_MCP_OAUTH_STATE_SECRET`
absent — une vraie misconfiguration serveur, qui casse la danse pour TOUS les
connecteurs — disparaîtrait derrière le bruit des refus normaux.
"""
from __future__ import annotations

from oto_mcp.error_taxonomy import _is_expected_error
from oto_mcp.oauth_flow import OAuthExchangeRefused, OAuthFlowError


def test_refusal_is_a_managed_error():
    assert _is_expected_error(
        OAuthExchangeRefused("Échec de l'échange OAuth (login.salesforce.com) : "
                             "invalid_grant : expired authorization code.")) is True


def test_a_bare_flow_error_is_still_a_bug():
    """`OTO_MCP_OAUTH_STATE_SECRET` manquante = notre configuration, pas celle du client."""
    assert _is_expected_error(
        OAuthFlowError("OTO_MCP_OAUTH_STATE_SECRET env var manquante")) is False


def test_refusal_is_seen_through_a_connector_translation():
    """Chaque connecteur re-lève SON message traduit `from e` : c'est la chaîne qui
    porte le refus, et la taxonomie n'a à connaître aucun connecteur par son nom."""
    try:
        try:
            raise OAuthExchangeRefused("Échec de l'échange OAuth (host) : invalid_grant.")
        except OAuthExchangeRefused as e:
            raise RuntimeError("le grant a été refusé — jeton révoqué ou expiré…") from e
    except RuntimeError as translated:
        assert _is_expected_error(translated) is True


def test_an_unrelated_error_is_untouched():
    assert _is_expected_error(KeyError("row_id")) is False
