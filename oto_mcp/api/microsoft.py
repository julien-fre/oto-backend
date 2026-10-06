"""Return route of the Microsoft connection — the only hand-written piece.

Same shape as `api/meta_ads.py`: `/start` goes through the common seam
(`connectors/flow`), only the callback is a route (Microsoft redirects a
BROWSER, with no auth header). The identity comes from the signed `state`.

⚠️ `error=access_denied` covers both a refusal by the person and an
organisation that requires administrator consent: the card says
"refused", and the connector text explains the second case.
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
from ..auth import microsoft as ms_auth

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
            return_app, ms_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        erreur = request.query_params.get("error")
        parsed = ms_auth.verify_state(state) if state else None
        if not parsed:
            logger.info("sharepoint: connection return without a readable state")
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, return_app = parsed
        if erreur or not code:
            # `error_description` may name the organisation: we only log
            # the error code.
            logger.info("sharepoint: connection not completed (sub=%s, reason=%s)",
                        sub, erreur or "missing code")
            return RedirectResponse(
                _retour("forbidden" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)

        def _echanger_et_ranger() -> dict:
            coordonnees = ms_auth.app()
            grant = ms_auth._coeur().auth.exchange_code(
                coordonnees["client_id"], coordonnees["client_secret"], code,
                oauth_flow.redirect_uri(ms_auth._CALLBACK_PATH))
            return ms_auth.persist_grant(sub, org_id, grant)

        try:
            # Synchronous DB + HTTP off the event loop (oto-backend#867).
            await run_in_threadpool(_echanger_et_ranger)
        except Exception:
            # We log the traceback, never the `code` or the token.
            logger.exception("sharepoint: connection return failed "
                             "(sub=%s org=%s)", sub, org_id)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(ms_auth._CALLBACK_PATH, callback, methods=["GET"])]
