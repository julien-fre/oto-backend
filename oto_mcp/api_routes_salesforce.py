"""Routes REST OAuth Salesforce — live "Connect" flow replacing the manual
Postman-style refresh-token acquisition (see salesforce_oauth.py's module
docstring for the per-customer-Connected-App architecture this works around).

Structure mirrors api_routes_folk.py / api_routes_atlassian.py:
- `GET /api/salesforce/oauth/callback` (no auth, Salesforce redirects) → exchange + persist

Le `/start` n'est PAS ici : c'est une capacité (`capabilities/salesforce_connect.py`,
ADR 0042 §Convergence des surfaces) qui en dérive les faces MCP et REST depuis un seul
descripteur. Seul le callback reste une route écrite à la main — un fournisseur y
redirige le NAVIGATEUR, sans auth et avec un 302, ce qu'un contrat de capacité ne peut
pas exprimer.

Unlike Folk/Atlassian, there is no `/status`/`DELETE` here yet — the
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
import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import salesforce_oauth

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
        return os.environ.get("OTO_APP_URL", "https://dashboard.oto.ninja").rstrip("/")

    async def callback(request: Request) -> Response:
        # Salesforce redirige ici (pas d'auth Logto) — l'identité + le scope
        # viennent du state signé (voir salesforce_oauth.make_state).
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = salesforce_oauth.verify_state(state) if state else None
        if not code or not parsed:
            return RedirectResponse(f"{_app_url()}/?salesforce=error", status_code=302)
        sub, org_id, scope, verifier_pkce, group_id = parsed
        # RE-GARDE du droit d'écrire au scope demandé. `build_auth_url` l'a vérifié
        # au /start, mais le state vit 10 min : entre le clic et le retour, l'auteur
        # a pu perdre son rôle. Doctrine maison (ADR 0038, ce qui a fermé #108) :
        # une autorisation se re-vérifie à la RÉSOLUTION, pas seulement à la pose.
        from . import roles
        allowed = True
        if scope == "org":
            allowed = roles.is_org_admin(sub, org_id)
        elif scope == "group":
            allowed = roles.can_admin_group(sub, group_id)
        if not allowed:
            logger.warning("salesforce callback refusé : %s n'est plus admin du scope "
                           "%s (org=%s group=%s)", sub, scope, org_id, group_id)
            return RedirectResponse(f"{_app_url()}/?salesforce=forbidden", status_code=302)
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
            # Le client ne voit qu'un `?salesforce=error` : sans trace ici, un échec de
            # connexion est INDIAGNOSTICABLE (Sentry ne voit rien, l'exception est
            # avalée). On journalise le traceback, jamais le `code` ni les tokens.
            logger.exception("salesforce oauth callback en échec (sub=%s scope=%s org=%s)",
                             sub, scope, org_id)
            return RedirectResponse(f"{_app_url()}/?salesforce=error", status_code=302)
        status = "connected" if result.get("verified") else "connected_unverified"
        return RedirectResponse(f"{_app_url()}/?salesforce={status}", status_code=302)

    return [Route("/api/salesforce/oauth/callback", callback, methods=["GET"])]
