"""Return from Slack's install consent — hand-written route, like the Meta Ads and
WordPress returns, and for the same reason: Slack sends the user's BROWSER back here,
with no auth header (the identity comes from the signed `state`), and the response is
a 302 to the front end. Declared as an exception in
`tests/test_rest_modules_are_capabilities.py`.

In order: state read, state CONSUMED (single use), provider refusal, right to write at
the tier RE-CHECKED (`forbidden`), then exchange and storage. The `code` and the
tokens never enter a log line nor the return URL.
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
from ..auth import slack as slack_auth

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
            parsed.get("app") or "", slack_auth.CONNECTOR, etat, org=parsed.get("org"))

    async def callback(request: Request) -> Response:
        q = request.query_params
        parsed = slack_auth.read_state(q.get("state"))
        if not parsed:
            logger.info("slack: consent return without a readable state")
            return RedirectResponse(_retour("error", None), status_code=302)
        if not await run_in_threadpool(oauth_flow.consume_state, slack_auth.AUD, parsed):
            logger.warning("slack: consent return with a replayed state (sub=%s)",
                           parsed["sub"])
            return RedirectResponse(_retour("error", parsed), status_code=302)
        erreur, code = q.get("error"), q.get("code")
        if erreur or not code:
            logger.info("slack: consent not completed (sub=%s, reason=%s)",
                        parsed["sub"], erreur or "code missing")
            return RedirectResponse(_retour(oauth_flow.etat_sans_code(erreur), parsed),
                                    status_code=302)
        if not await run_in_threadpool(slack_auth.still_allowed, parsed):
            logger.warning("slack: consent return refused: %s no longer has the right "
                           "to write at tier %s (org=%s group=%s)", parsed["sub"],
                           parsed["scope"], parsed["org"], parsed.get("group"))
            return RedirectResponse(_retour("forbidden", parsed), status_code=302)

        def _echanger_et_ranger() -> str:
            return slack_auth.finish(parsed, slack_auth.exchange(code))

        try:
            # Synchronous HTTP + DB off the loop (oto-backend#867).
            await run_in_threadpool(_echanger_et_ranger)
        except oauth_flow.OAuthExchangeRefused as e:
            logger.info("slack: exchange refused (sub=%s org=%s): %s",
                        parsed["sub"], parsed["org"], e)
            return RedirectResponse(_retour("error", parsed), status_code=302)
        except Exception:
            # The traceback, never the `code` nor a token.
            logger.exception("slack: consent return failed (sub=%s org=%s)",
                             parsed["sub"], parsed["org"])
            return RedirectResponse(_retour("error", parsed), status_code=302)
        return RedirectResponse(_retour("connected", parsed), status_code=302)

    return [Route(slack_auth.CALLBACK_PATH, callback, methods=["GET"])]
