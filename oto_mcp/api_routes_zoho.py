"""Retour de consentement OAuth Zoho — la SEULE route qui reste écrite à la main.

Zoho redirige ici le **navigateur** de l'utilisateur : pas d'en-tête d'auth (l'identité
vient du `state` signé) et la réponse est un **302** vers le dashboard. Un contrat de
capacité (JSON + autz) ne peut pas exprimer ça — d'où l'exception, déclarée comme telle
dans `tests/test_rest_modules_are_capabilities.py`.

Les verbes qui l'accompagnent (`start`, `modes`) sont, eux, des **capacités**
(`capabilities/zoho_connect.py`, ADR 0042 §Convergence des surfaces) : une déclaration,
deux faces dérivées (REST pour le dashboard, MCP pour l'agent), une seule autz.

Une seule URI de redirection sert les TROIS connecteurs Zoho — le connecteur voyage
dans le `state` — car une URI s'enregistre au byte près côté Zoho : une seule à
déclarer par app au lieu de trois.
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

from . import zoho_oauth
from . import config

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

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        parsed = zoho_oauth.verify_state(state) if state else None
        if not code or not parsed:
            return RedirectResponse(f"{_app_url()}/console/connectors?zoho=error",
                                    status_code=302)

        def _finish() -> None:
            # ⚠️ La RÉGION doit être repassée : l'app d'éditeur est keyée par data
            # center. Sans elle, `app_fields` ne verrait que le BYO et l'échange du
            # code échouerait pour tout utilisateur venu par l'app d'oto — alors même
            # que le consentement, lui, a réussi.
            app = zoho_oauth.app_fields(parsed["connector"], parsed["sub"],
                                        parsed["data_center"])
            tokens = zoho_oauth.exchange_code(code, parsed["data_center"], app=app)
            zoho_oauth.persist(parsed["sub"], parsed["org"], parsed["connector"],
                               parsed["data_center"], tokens, app=app)

        try:
            await run_in_threadpool(_finish)   # DB + HTTP sync → hors event loop
        except Exception as e:  # noqa: BLE001
            # Jamais le détail dans l'URL (il pourrait porter un message amont) ;
            # le diagnostic va au journal, sans secret (#284).
            logger.warning("zoho oauth callback failed: %s", type(e).__name__)
            return RedirectResponse(
                f"{_app_url()}/console/connectors?zoho=error", status_code=302)
        return RedirectResponse(
            f"{_app_url()}/console/connectors?{parsed['connector']}=connected",
            status_code=302)

    return [Route("/api/zoho/oauth/callback", callback, methods=["GET"])]
