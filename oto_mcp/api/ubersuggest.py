"""Return route of the Ubersuggest connection — the only hand-written piece.

Same shape as `api/microsoft.py`: `/start` goes through the common seam
(`connectors/flow`), only the callback is a route (Ubersuggest redirects a
BROWSER, with no auth header). The identity, the PKCE verifier and the OAuth client
come from the signed `state`.

In order, like `api/wordpress.py`: state read, state CONSUMED (single use), the
person's refusal, membership and connector RE-CHECKED (`forbidden`), then exchange
and storage. The query carries the code and, in the state, the PKCE verifier — for
a public client the two together are a token: the route's query is stripped from
the access log and from Sentry (`journal_secrets.routes_a_requete_secrete`).
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
from ..auth import ubersuggest as ub_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

_REFUS = "access_denied"


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _retour(etat: str, return_app: str = "", org_id: int | None = None) -> str:
        return oauth_flow.connector_return_url(
            return_app, ub_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        erreur = request.query_params.get("error")
        parsed = ub_auth.verify_state(state) if state else None
        if not parsed:
            logger.info("ubersuggest: connection return without a readable state")
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, return_app = parsed["sub"], parsed["org"], parsed["app"]
        if not await run_in_threadpool(oauth_flow.consume_state, ub_auth._AUD, parsed):
            logger.warning("ubersuggest: connection return with a replayed state (sub=%s)",
                           sub)
            return RedirectResponse(_retour("error", return_app, org_id), status_code=302)
        if erreur or not code:
            logger.info("ubersuggest: connection not completed (sub=%s, reason=%s)",
                        sub, erreur or "missing code")
            return RedirectResponse(
                _retour("denied" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)
        if not await run_in_threadpool(ub_auth.still_allowed, parsed):
            logger.warning("ubersuggest: connection return refused: %s is no longer a "
                           "member of org %s, or the connector is now cut there",
                           sub, org_id)
            return RedirectResponse(_retour("forbidden", return_app, org_id),
                                    status_code=302)

        try:
            # Synchronous DB + HTTP off the event loop (oto-backend#867).
            await run_in_threadpool(ub_auth.exchange_and_persist, parsed, code)
        except Exception:
            # We log the traceback, never the `code` or the token.
            logger.exception("ubersuggest: connection return failed (sub=%s org=%s)",
                             sub, org_id)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(ub_auth.CALLBACK_PATH, callback, methods=["GET"])]
