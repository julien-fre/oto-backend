"""Routes REST OAuth Zoho — mode **server-based** (le second mode d'acquisition).

Une seule paire de routes sert les TROIS connecteurs Zoho (`zoho`, `zohodesk`,
`zohoanalytics`) : le connecteur voyage dans le `state` signé, ce qui évite
d'enregistrer trois URI de redirection par app côté Zoho (elles doivent être
déclarées au byte près).

- `GET /api/zoho/oauth/start?connector=…&data_center=…` (auth) → `{auth_url}`
- `GET /api/zoho/oauth/callback` (SANS auth — Zoho redirige le navigateur) →
  échange le code, range le credential, renvoie l'utilisateur au dashboard.

Le mode **Self Client** reste intact et reste le défaut : ces routes n'y touchent
pas, elles ajoutent un chemin. Les deux produisent le même credential.
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

from . import access, credentials_store, zoho_oauth

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

    def _org_app(sub: str, connector: str) -> dict:
        """Repli « app de l'org » : si l'org a déjà posé client_id/client_secret sur
        la carte, on s'en sert pour piloter quand même les scopes. Best-effort —
        l'absence de credential est le cas NOMINAL d'une première connexion."""
        try:
            return access.resolve_credential_fields(connector, sub=sub) or {}
        except Exception:  # noqa: BLE001 — pas encore de credential : normal
            return {}

    async def start(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        connector = (request.query_params.get("connector") or "").strip()
        dc = (request.query_params.get("data_center") or "").strip().lower()
        if not zoho_oauth.supports(connector):
            return json_error(request, 400, "unknown_zoho_connector")

        def _build() -> str:
            org_id = access.current_org(sub) or 0
            return zoho_oauth.build_auth_url(
                sub, org_id, connector, dc, org_app=_org_app(sub, connector))

        try:
            url = await run_in_threadpool(_build)   # DB sync → hors event loop
        except zoho_oauth.ZohoOAuthError as e:
            return json_error(request, 400, str(e))
        return json_response(request, {"auth_url": url})

    async def callback(request: Request) -> Response:
        # Zoho redirige le NAVIGATEUR ici : pas d'en-tête d'auth, l'identité vient
        # du state signé. On repart vers le dashboard dans tous les cas (l'utilisateur
        # est dans son navigateur, pas dans un client d'API).
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = zoho_oauth.verify_state(state) if state else None
        if not code or not parsed:
            return RedirectResponse(f"{_app_url()}/console/connectors?zoho=error",
                                    status_code=302)

        def _finish() -> None:
            app = _org_app(parsed["sub"], parsed["connector"])
            tokens = zoho_oauth.exchange_code(code, parsed["data_center"], org_app=app)
            zoho_oauth.persist(parsed["sub"], parsed["org"], parsed["connector"],
                               parsed["data_center"], tokens, org_app=app)

        try:
            await run_in_threadpool(_finish)
        except Exception as e:  # noqa: BLE001
            # Jamais le détail à l'utilisateur via l'URL (il pourrait porter un
            # message amont) ; le diagnostic va au journal, sans secret (#284).
            logger.warning("zoho oauth callback failed: %s", type(e).__name__)
            return RedirectResponse(
                f"{_app_url()}/console/connectors?zoho=error", status_code=302)
        return RedirectResponse(
            f"{_app_url()}/console/connectors?{parsed['connector']}=connected",
            status_code=302)

    async def modes(request: Request) -> JSONResponse:
        """Ce que le front doit savoir pour afficher le bon écran : le connecteur
        supporte-t-il le server-based, et une app de plateforme existe-t-elle pour
        cette région (sinon l'org doit d'abord poser son client_id/secret) ?"""
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        connector = (request.query_params.get("connector") or "").strip()
        dc = (request.query_params.get("data_center") or "").strip().lower()
        if not zoho_oauth.supports(connector):
            return json_error(request, 400, "unknown_zoho_connector")
        return json_response(request, {
            "connector": connector,
            "self_client": True,          # toujours disponible
            "server_based": True,
            "platform_app": zoho_oauth.platform_app(dc) is not None,
            "scopes": list(zoho_oauth.SCOPES[connector]),
        })

    return [
        Route("/api/zoho/oauth/start", start, methods=["GET"]),
        Route("/api/zoho/oauth/start", options_handler, methods=["OPTIONS"]),
        Route("/api/zoho/oauth/callback", callback, methods=["GET"]),
        Route("/api/zoho/oauth/modes", modes, methods=["GET"]),
        Route("/api/zoho/oauth/modes", options_handler, methods=["OPTIONS"]),
    ]
