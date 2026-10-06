"""Zoho OAuth consent return — the ONLY route still hand-written.

Zoho redirects the user's **browser** here: no auth header (the identity comes from
the signed `state`) and the response is a **302** to the dashboard. A capability
contract (JSON + authz) cannot express that — hence the exception, declared as such
in `tests/test_rest_modules_are_capabilities.py`.

The verbs that go with it (`start`, `modes`) are **capabilities**
(`capabilities/zoho_connect.py`, ADR 0042 §Surface convergence): one declaration,
two derived faces (REST for the dashboard, MCP for the agent), one authz.

A single redirect URI serves ALL THREE Zoho connectors — the connector travels
in the `state` — because a URI is registered byte for byte on the Zoho side: only one
to declare per app instead of three.
"""
from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..auth import zoho as zoho_oauth
from .. import config

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _app_url() -> str:
        return config.dashboard_url()

    def _retour(etat: str, return_app: str = "", org_id: "int | None" = None,
               connector: str = "zoho") -> str:
        """Where to send the browser back after the Zoho consent.

        `return_app` = the front that REQUESTED the connection, read back from the signed
        state (`""` when the state could not be read: we then have no way of knowing who
        to call back, an accepted degraded case). While this URL was hard-coded to
        oto-dashboard, a user of a partner front ended their consent
        on another product.

        The default stays `/console/connectors` BYTE FOR BYTE: it is a path specific
        to the dashboard, which the generic `return_url` pattern does not know — falling
        back to it would send the historical caller to `/connectors`.

        Single OAuth return convention (oto-backend#670): the suffix comes
        from the shared maker `oauth_flow.connector_return_suffix`. The old
        form `?<connector>=connected` / `?zoho=error`, duplicated for the length of a
        notice period, was removed on 17/09/2026 — no reader measured any more (the
        dashboard has read `connect=` since 04/09, and no Zoho connection exists
        at the only partner concerned, Tulina)."""
        from ..auth import flow as oauth_flow
        suffix = oauth_flow.connector_return_suffix(connector, etat)
        if oauth_flow.resolve_return_app(return_app):
            return oauth_flow.return_url(return_app, suffix, org=org_id)
        return f"{_app_url()}/console/connectors{suffix}"

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = zoho_oauth.verify_state(state) if state else None
        if not code or not parsed:
            # Unreadable state: neither `return_app` nor `org` (they live inside it), nor the
            # real connector — `_retour` falls back to its default ("zoho").
            return RedirectResponse(_retour("error"),
                                    status_code=302)

        def _finish() -> None:
            # ⚠️ The REGION must be passed again: the publisher app is keyed by data
            # center. Without it, `app_fields` would only see the BYO and the code
            # exchange would fail for every user who came through oto's app — even
            # though the consent itself succeeded.
            app = zoho_oauth.app_fields(parsed["connector"], parsed["sub"],
                                        parsed["data_center"])
            tokens = zoho_oauth.exchange_code(code, parsed["data_center"], app=app)
            zoho_oauth.persist(parsed["sub"], parsed["org"], parsed["connector"],
                               parsed["data_center"], tokens, app=app)

        try:
            await run_in_threadpool(_finish)   # sync DB + HTTP → off the event loop
        except Exception as e:  # noqa: BLE001
            # Never the detail in the URL (it could carry an upstream message);
            # the diagnosis goes to the log, without secrets (#284).
            logger.warning("zoho oauth callback failed: %s", type(e).__name__)
            return RedirectResponse(
                _retour("error", parsed["return_app"], parsed["org"],
                        parsed["connector"]),
                status_code=302)
        return RedirectResponse(
            _retour("connected", parsed["return_app"], parsed["org"],
                    parsed["connector"]),
            status_code=302)

    return [Route("/api/zoho/oauth/callback", callback, methods=["GET"])]
