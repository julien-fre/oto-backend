"""Return route of the Instagram consent — the only hand-written piece.

`/start` is NOT here: it is the common seam (`connectors/flow`), whose declaration
is `auth/instagram_meta._start_flow`. Only the callback remains a
route: Meta redirects a BROWSER there, with no auth header and with a 302, which
a capability contract cannot express. Same shape as `api/salesforce.py`.

The person's identity comes from the signed `state`, not from a session: it is all
this handler has, and that is why the state carries `sub` and `org`.

⚠️ **The most frequent refusal of this flow is not a bug, it is the regime of
unpublished Meta apps**: until it has passed App Review, only accounts INVITED
as testers can consent. Meta then returns an
`error=access_denied`, indistinguishable from a real refusal by the person. The message
therefore names both causes, in this order — because the first is ours to
fix, and telling someone who clicked "Allow" that "you refused"
leaves them with no recourse.
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
from ..auth import instagram_meta as ig_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

#: The reason Meta returns when the person is not a tester of the application.
#: It is the SAME as a real refusal: `access_denied`. We therefore cannot
#: tell them apart — hence a message that names both, without asserting either.
_REFUS = "access_denied"


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _retour(etat: str, return_app: str = "", org_id: int | None = None) -> str:
        """Where to send the browser: to the connector card, expanded.

        `connector=` is the deep link read by the dashboard, `connect=` says WHAT
        HAPPENED — without it, a person comes back to a silent screen and cannot
        know whether their action succeeded."""
        return oauth_flow.connector_return_url(
            return_app, ig_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        erreur = request.query_params.get("error")
        parsed = ig_auth.verify_state(state) if state else None
        if not parsed:
            # State missing, expired or tampered with: we have NEITHER org NOR return front
            # (they live INSIDE this state that we just failed to read). Degradation
            # accepted towards the default, the only case where this module cannot do better.
            logger.info("instagram_meta: consent return without a readable state")
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, return_app = parsed
        if erreur or not code:
            # We LOG the reason returned by Meta (it is not secret) and we
            # send the user back to her card, where the message below
            # awaits her. See `oto_mcp/connectors/docs/instagram_meta.md`.
            logger.info("instagram_meta: consent not completed (sub=%s, reason=%s)",
                        sub, erreur or "missing code")
            return RedirectResponse(
                _retour("forbidden" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)

        def _echanger_et_ranger() -> dict:
            grant = ig_auth._coeur().connect(
                ig_auth.app(), code,
                oauth_flow.redirect_uri(ig_auth._CALLBACK_PATH))
            return ig_auth.persist_grant(sub, org_id, grant)

        try:
            # Synchronous DB + HTTP off the event loop: this handler is `async def`,
            # and the exchange talks to Meta three times. Called bare it freezes the whole
            # process while upstream answers (oto-backend#867).
            await run_in_threadpool(_echanger_et_ranger)
        except Exception:
            # Without a trace here, a connection failure is UNDIAGNOSABLE: the
            # client only sees a `connect=error`. We log the traceback,
            # never the `code` or the token.
            logger.exception("instagram_meta: consent return failed "
                             "(sub=%s org=%s)", sub, org_id)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(ig_auth._CALLBACK_PATH, callback, methods=["GET"])]
