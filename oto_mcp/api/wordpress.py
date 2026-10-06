"""Return from the WordPress authorization screen — hand-written route, like the
Zoho OAuth return, and for the same reason: it is the user's BROWSER
that the site sends back here, with no auth header (the identity comes from the signed `state`),
and the response is a 302 to the front end. Declared as an exception in
`tests/test_rest_modules_are_capabilities.py`.

In order: state read, state CONSUMED (single use), user refusal,
right to write at the tier RE-CHECKED (`forbidden`), then verification and storage.

⚠️ **This URL carries a password in the query string** (`password=`, WordPress's
protocol, not a choice). Its query is stripped from the access log and from Sentry
by the common list `journal_secrets.routes_a_requete_secrete`. Never an error's
detail in the return URL, never the password in a log
message.
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
            logger.warning("wordpress connect callback refused: %s no longer has the right "
                           "to write at tier %s (org=%s group=%s)", parsed["sub"],
                           parsed["scope"], parsed["org"], parsed.get("group"))
            return RedirectResponse(_retour("forbidden", parsed), status_code=302)

        def _finish() -> str:
            return wp_auth.finish(parsed, q.get("site_url") or "", q.get("user_login") or "",
                                  q.get("password") or "")

        try:
            await run_in_threadpool(_finish)
        except Exception as e:  # noqa: BLE001 — failure is signalled by the return, never in detail
            logger.warning("wordpress connect callback failed: %s", type(e).__name__)
            return RedirectResponse(_retour("error", parsed), status_code=302)
        return RedirectResponse(_retour("connected", parsed), status_code=302)

    return [Route(wp_auth.CALLBACK_PATH, callback, methods=["GET"])]
