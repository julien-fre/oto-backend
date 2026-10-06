"""Return route for the Meta Ads consent — the only hand-written piece.

Same shape as `api/instagram_meta.py`: `/start` goes through the common seam
(`connectors/flow`), only the callback is a route (Meta redirects a BROWSER,
with no auth header). The identity comes from the signed `state`.

⚠️ As long as the application does not have the required access, Meta may refuse the dialog
with `error=access_denied` — indistinguishable from a genuine refusal by the person.
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
from ..auth import meta_ads as ads_auth

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
            return_app, ads_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        erreur = request.query_params.get("error")
        parsed = ads_auth.verify_state(state) if state else None
        if not parsed:
            logger.info("meta_ads: consent return without a readable state")
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, return_app = parsed
        if erreur or not code:
            logger.info("meta_ads: consent not completed (sub=%s, reason=%s)",
                        sub, erreur or "code missing")
            return RedirectResponse(
                _retour("forbidden" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)

        def _echanger_et_ranger() -> dict:
            grant = ads_auth._coeur().connect(
                ads_auth.app(), code,
                oauth_flow.redirect_uri(ads_auth._CALLBACK_PATH))
            return ads_auth.persist_grant(sub, org_id, grant)

        try:
            # Synchronous DB + HTTP off the loop (oto-backend#867).
            await run_in_threadpool(_echanger_et_ranger)
        except Exception:
            # We log the traceback, never the `code` nor the token.
            logger.exception("meta_ads: consent return failed "
                             "(sub=%s org=%s)", sub, org_id)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(ads_auth._CALLBACK_PATH, callback, methods=["GET"])]
