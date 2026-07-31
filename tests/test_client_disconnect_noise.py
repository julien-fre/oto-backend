"""Déconnexion client en cours de réponse : bruit de transport, jamais un event Sentry.

Le client MCP ferme son POST (onglet fermé, conversation abandonnée) pendant que le
serveur écrit la réponse. Deux formes du même incident, chaînées dans le même event
Sentry — 38 événements en 3 semaines, aucun actionnable.

Invariant CLÉ vérifié ici : le drop est fait par `_before_send` (Sentry), PAS par
`_is_expected_error` — ce dernier sert aussi à composer la réponse rendue à l'agent,
et une déconnexion client n'atteint aucun agent.
"""
from __future__ import annotations

import anyio

from oto_mcp.error_taxonomy import _is_client_disconnect, _is_expected_error
from oto_mcp.sentry_setup import _before_send


def _exc(e: BaseException) -> tuple:
    return (type(e), e, None)


def test_closed_resource_error_is_a_client_disconnect():
    assert _is_client_disconnect(anyio.ClosedResourceError()) is True


def test_asgi_after_complete_is_a_client_disconnect():
    e = RuntimeError("Unexpected ASGI message 'http.response.start' sent, "
                     "after response already completed.")
    assert _is_client_disconnect(e) is True


def test_detected_through_the_cause_chain():
    # Forme réelle de l'event : uvicorn lève le RuntimeError ASGI *à cause du*
    # ClosedResourceError du SDK MCP.
    try:
        try:
            raise anyio.ClosedResourceError()
        except anyio.ClosedResourceError as cause:
            raise RuntimeError("boom") from cause
    except RuntimeError as e:
        assert _is_client_disconnect(e) is True


def test_before_send_drops_them():
    assert _before_send({"x": 1}, {"exc_info": _exc(anyio.ClosedResourceError())}) is None


def test_real_bug_still_reported():
    assert _is_client_disconnect(KeyError("orgs")) is False
    assert _before_send({"x": 1}, {"exc_info": _exc(KeyError("orgs"))}) == {"x": 1}


def test_stays_out_of_the_agent_facing_predicate():
    # L'invariant de conception : ces erreurs ne sont PAS « attendues » au sens de
    # l'enveloppe agent — elles sont hors de son plan. Si quelqu'un les fait glisser
    # dans `_is_expected_error`, ce test tombe et le lui dit.
    assert _is_expected_error(anyio.ClosedResourceError()) is False
