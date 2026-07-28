"""Routes REST OAuth Salesforce — live "Connect" flow replacing the manual
Postman-style refresh-token acquisition (see salesforce_oauth.py's module
docstring for the per-customer-Connected-App architecture this works around).

Structure mirrors api_routes_folk.py / api_routes_atlassian.py:
- `GET /api/salesforce/oauth/start`    (auth Logto) → {auth_url} to open
- `GET /api/salesforce/oauth/callback` (no auth, Salesforce redirects) → exchange + persist

Unlike Folk/Atlassian/Memento, there is no `/status`/`DELETE` here yet — the
existing generic `/api/settings/api-keys/salesforce` GET/DELETE already covers
status/disconnect for this connector (it's still `secret_kind="fields"`,
`auth_method="secret_then_oauth"` — client_id/client_secret/login_url are
pasted through the normal form, only refresh_token comes from this flow now,
not pasted at all anymore).

`scope` (`?scope=member|org|group`) selects which credential row `/start`
reads from and `/callback` writes to — mirrors the same three levels the
static-fields form already supports via `/api/settings/api-keys/salesforce`,
`PUT /api/orgs/{id}/secrets/salesforce`, and `PUT /api/groups/{id}/secrets/salesforce`.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import salesforce_oauth

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _app_url() -> str:
        return os.environ.get("OTO_APP_URL", "https://dashboard.oto.ninja").rstrip("/")

    async def start(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        from mcp.shared.exceptions import McpError
        from . import access
        try:
            access.require_connector_access("salesforce", sub)
        except McpError as e:
            return json_error(request, 403, "connector_restricted", e.error.message)

        scope = request.query_params.get("scope", "member")
        try:
            auth_url = salesforce_oauth.build_auth_url(sub, scope)
        except ValueError as e:
            return json_error(request, 400, "invalid_scope_param", str(e))
        except PermissionError as e:
            return json_error(request, 403, "org_admin_required", str(e))
        except LookupError as e:
            return json_error(request, 400, "missing_credentials", str(e))
        except RuntimeError as e:
            return json_error(request, 400, "oauth_misconfigured", str(e))
        return json_response(request, {"auth_url": auth_url})

    async def callback(request: Request) -> Response:
        # Salesforce redirige ici (pas d'auth Logto) — l'identité + le scope
        # viennent du state signé (voir salesforce_oauth.make_state).
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = salesforce_oauth.verify_state(state) if state else None
        if not code or not parsed:
            return RedirectResponse(f"{_app_url()}/?salesforce=error", status_code=302)
        sub, org_id, scope, verifier_pkce, group_id = parsed
        try:
            fields = salesforce_oauth.read_saved_fields(sub, org_id, scope, group_id)
            if not fields:
                raise RuntimeError("Credential introuvable au retour de Salesforce.")
            tokens = salesforce_oauth.exchange_code(
                code,
                client_id=fields["client_id"],
                client_secret=fields["client_secret"],
                login_url=fields["login_url"],
                verifier=verifier_pkce,
            )
            result = await salesforce_oauth.persist_token(sub, org_id, scope, tokens, group_id)
        except Exception:
            return RedirectResponse(f"{_app_url()}/?salesforce=error", status_code=302)
        status = "connected" if result.get("verified") else "connected_unverified"
        return RedirectResponse(f"{_app_url()}/?salesforce={status}", status_code=302)

    return [
        Route("/api/salesforce/oauth/start", start, methods=["GET"]),
        Route("/api/salesforce/oauth/start", options_handler, methods=["OPTIONS"]),
        Route("/api/salesforce/oauth/callback", callback, methods=["GET"]),
    ]
