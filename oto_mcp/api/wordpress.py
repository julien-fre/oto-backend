"""Retour de l'écran d'autorisation WordPress — route écrite à la main, comme le
retour OAuth Zoho, et pour la même raison : c'est le NAVIGATEUR de l'utilisateur
que le site renvoie ici, sans en-tête d'auth (l'identité vient du `state` signé),
et la réponse est un 302 vers le front. Déclarée comme exception dans
`tests/test_rest_modules_are_capabilities.py`.

Dans l'ordre : state lu, state CONSOMMÉ (usage unique), refus de l'utilisateur,
droit d'écrire au palier RE-VÉRIFIÉ (`forbidden`), puis vérification et pose.

⚠️ **Cette URL porte un mot de passe en query string** (`password=`, protocole
de WordPress, pas un choix). Sa query est retirée du journal d'accès et de Sentry
par la liste commune `journal_secrets.routes_a_requete_secrete`. Jamais le détail
d'une erreur dans l'URL de retour, jamais le mot de passe dans un message de
journal.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..auth import flow as oauth_flow
from ..auth import wordpress as wp_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _retour(etat: str, parsed: dict | None) -> str:
        parsed = parsed or {}
        return oauth_flow.connector_return_url(
            parsed.get("app") or "", "wordpress", etat, org=parsed.get("org"))

    async def callback(request: Request) -> Response:
        q = request.query_params
        parsed = wp_auth.read_state(q.get("state"))
        if not parsed:
            return RedirectResponse(_retour("error", None), status_code=302)
        if not await run_in_threadpool(oauth_flow.consume_state, wp_auth.AUD, parsed):
            logger.warning("wordpress connect callback: state replayed (sub=%s)",
                           parsed["sub"])
            return RedirectResponse(_retour("error", parsed), status_code=302)
        if q.get("success") == "false":
            return RedirectResponse(_retour("denied", parsed), status_code=302)
        if not await run_in_threadpool(wp_auth.still_allowed, parsed):
            logger.warning("wordpress connect callback refusé : %s n'a plus le droit "
                           "d'écrire au palier %s (org=%s group=%s)", parsed["sub"],
                           parsed["scope"], parsed["org"], parsed.get("group"))
            return RedirectResponse(_retour("forbidden", parsed), status_code=302)

        def _finish() -> str:
            return wp_auth.finish(parsed, q.get("site_url") or "", q.get("user_login") or "",
                                  q.get("password") or "")

        try:
            await run_in_threadpool(_finish)
        except Exception as e:  # noqa: BLE001 — l'échec se dit par le retour, jamais en détail
            logger.warning("wordpress connect callback failed: %s", type(e).__name__)
            return RedirectResponse(_retour("error", parsed), status_code=302)
        return RedirectResponse(_retour("connected", parsed), status_code=302)

    return [Route(wp_auth.CALLBACK_PATH, callback, methods=["GET"])]
