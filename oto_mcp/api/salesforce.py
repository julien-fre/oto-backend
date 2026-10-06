"""Salesforce OAuth REST routes — live "Connect" flow replacing the manual
Postman-style refresh-token acquisition (see salesforce_oauth.py's module
docstring for the per-customer-Connected-App architecture this works around).

Structure (shared with the OAuth callback modules that preceded it):
- `GET /api/salesforce/oauth/callback` (no auth, Salesforce redirects) → exchange + persist

The `/start` is NOT here: it is a capability (`capabilities/salesforce_connect.py`,
ADR 0042 §Surface convergence) from which the MCP and REST faces are derived out of a single
descriptor. Only the callback remains a hand-written route — a provider
redirects the BROWSER there, with no auth and with a 302, which a capability contract cannot
express.

There is no `/status`/`DELETE` here yet — the
existing generic `/api/settings/api-keys/salesforce` GET/DELETE already covers
status/disconnect for this connector (it's still `secret_kind="fields"`,
`secret_kind="fields"` — client_id/client_secret/login_url are
pasted through the normal form, only refresh_token comes from this flow now,
not pasted at all anymore).

`scope` (`?scope=member|org|group`) selects which credential row `/start`
reads from and `/callback` writes to — mirrors the same three levels the
static-fields form already supports via `/api/settings/api-keys/salesforce`,
`PUT /api/orgs/{id}/secrets/salesforce`, and `PUT /api/groups/{id}/secrets/salesforce`.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..auth import salesforce as salesforce_oauth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _retour(etat: str, return_app: str = "", org_id: int | None = None) -> str:
        """Where to send the browser back after consent: to the connector's card,
        expanded. `connector=` is the deep link read by the dashboard —
        without it we fall back to the list, and the row has to be found by hand.

        `return_app`/`org_id`: the FRONT that requested the connection (`""` =
        historical, degrades to `OTO_APP_URL`/oto-dashboard) — `oauth_flow.return_url`
        carries the base+path resolution, cf. its docstring. Absent in the ONE
        branch where the state could not even be read (see `callback` below):
        accepted degraded case, we then have no way of knowing who to call back.

        `connect=` says WHAT HAPPENED. The parameter used to be called `salesforce=` and
        **nobody read it**: the user came back to a silent screen. Lived on
        04/08 at a customer — the consent had SUCCEEDED (token set at the
        millisecond of the callback, zero errors), and for lack of the slightest sign they
        uninstalled then reinstalled the connector in a loop for five hours.
        Generic name: a key named after a connector would force every surface
        to know its name, exactly what we are removing everywhere else.

        Salesforce IS the target shape (oto-backend#670): `connector_return_url`
        (the shared maker) only does here what this function already composed
        by hand — no behaviour change, salesforce has nothing to double."""
        from ..auth import flow as oauth_flow
        return oauth_flow.connector_return_url(
            return_app, "salesforce", etat, org=org_id)

    async def callback(request: Request) -> Response:
        # Salesforce redirects here (no Logto auth) — the identity + the scope
        # come from the signed state (see salesforce_oauth.make_state).
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = salesforce_oauth.verify_state(state) if state else None
        if not code or not parsed:
            # State missing/expired/tampered: we have NEITHER org_id NOR return_app (they live
            # IN this state that we just failed to read) — accepted degradation to
            # the oto-dashboard default, the only case where this module cannot do better.
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, scope, verifier_pkce, group_id, return_app = parsed
        # RE-GUARD of the right to write at the requested scope. `build_auth_url` checked it
        # at /start, but the state lives 10 min: between the click and the return, the author
        # may have lost their role. In-house stance (ADR 0038, what closed #108):
        # an authorization is re-checked at RESOLUTION, not only at set time.
        from .. import roles

        def _droit_d_ecrire() -> bool:
            if scope == "org":
                return roles.is_org_admin(sub, org_id)
            if scope == "group":
                return roles.can_admin_group(sub, group_id)
            return True

        # Two role reads in the database: off the loop (public route, no token).
        if not await run_in_threadpool(_droit_d_ecrire):
            logger.warning("salesforce callback refused: %s is no longer admin of scope "
                           "%s (org=%s group=%s)", sub, scope, org_id, group_id)
            return RedirectResponse(_retour("forbidden", return_app, org_id), status_code=302)
        def _lire_et_echanger() -> dict:
            fields = salesforce_oauth.read_saved_fields(sub, org_id, scope, group_id)
            if not fields:
                raise RuntimeError("Credential not found on return from Salesforce.")
            return salesforce_oauth.exchange_code(
                code,
                client_id=fields["client_id"],
                client_secret=fields["client_secret"],
                login_url=fields["login_url"],
                verifier=verifier_pkce,
            )

        try:
            # Synchronous DB + HTTP off the loop: this handler is
            # `async def`, and the code exchange talks to a remote
            # server (15 to 30 s of waiting). Called bare it freezes the whole
            # process while upstream answers
            # (oto-backend#867). Same shape as the Zoho callback, which
            # was already protected — the discipline existed, it simply
            # had not been applied here.
            tokens = await run_in_threadpool(_lire_et_echanger)
            result = await run_in_threadpool(
                salesforce_oauth.persist_token, sub, org_id, scope, tokens, group_id)
        except Exception:
            # The client only sees a `?salesforce=error`: without a trace here, a connection
            # failure is UNDIAGNOSABLE (Sentry sees nothing, the exception is
            # swallowed). We log the traceback, never the `code` nor the tokens.
            logger.exception("salesforce oauth callback failed (sub=%s scope=%s org=%s)",
                             sub, scope, org_id)
            return RedirectResponse(_retour("error", return_app, org_id), status_code=302)
        # We come back to THE connector's card, not to the home page. The return
        # used to land on `/`, hence on the overview — a screen where Salesforce
        # appears nowhere: the user had just authorized and found themselves
        # facing nothing, with no way to see the result of their action.
        # `connected_unverified` disappeared with the post-write probe: this state
        # no longer exists, and the word "unverified" worried people for a healthy connection.
        del result  # setting IS the result; no more verdict to carry
        return RedirectResponse(_retour("connected", return_app, org_id), status_code=302)

    return [Route("/api/salesforce/oauth/callback", callback, methods=["GET"])]
