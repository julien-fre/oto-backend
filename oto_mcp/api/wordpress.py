"""Retour de l'écran d'autorisation WordPress — route écrite à la main, comme le
retour OAuth Zoho, et pour la même raison : c'est le NAVIGATEUR de l'utilisateur
que le site renvoie ici, sans en-tête d'auth (l'identité vient du `state` signé),
et la réponse est un 302 vers le front. Déclarée comme exception dans
`tests/test_rest_modules_are_capabilities.py`.

⚠️ **Cette URL porte un mot de passe en query string** (`password=`, protocole
de WordPress, pas un choix). Deux fuites fermées ici :
- le journal d'accès d'uvicorn — un filtre retire la query de CETTE route ;
- Sentry — `sentry_setup` retire la query des routes listées dans
  `SENSITIVE_QUERY_PATHS`.
Jamais le détail d'une erreur dans l'URL de retour, jamais le mot de passe dans
un message de journal.
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
from ..auth import wordpress as wp_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

SENSITIVE_QUERY_PATHS = (wp_auth.CALLBACK_PATH,)


class _RedactSensitiveQuery(logging.Filter):
    """Retire la query string des lignes du journal d'accès d'uvicorn pour les
    routes qui reçoivent un secret en query. Les arguments d'une ligne d'accès
    sont `(client, méthode, chemin_complet, version_http, statut)`."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if "?" in path and path.split("?", 1)[0] in SENSITIVE_QUERY_PATHS:
                record.args = (*args[:2], path.split("?", 1)[0] + "?[redacted]", *args[3:])
        return True


def install_log_redaction() -> None:
    """Idempotent — posé au montage des routes, jamais à l'import."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactSensitiveQuery) for f in access.filters):
        access.addFilter(_RedactSensitiveQuery())


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:
    install_log_redaction()

    def _retour(etat: str, parsed: dict | None) -> str:
        parsed = parsed or {}
        return oauth_flow.connector_return_url(
            parsed.get("app") or "", "wordpress", etat, org=parsed.get("org"))

    async def callback(request: Request) -> Response:
        q = request.query_params
        parsed = wp_auth.read_state(q.get("state"))
        if not parsed:
            return RedirectResponse(_retour("error", None), status_code=302)
        if q.get("success") == "false":
            return RedirectResponse(_retour("denied", parsed), status_code=302)

        def _finish() -> str:
            return wp_auth.finish(parsed, q.get("site_url") or "", q.get("user_login") or "",
                                  q.get("password") or "")

        try:
            await run_in_threadpool(_finish)
        except Exception as e:  # noqa: BLE001 — l'échec se dit par le retour, jamais en détail
            logger.warning("wordpress connect callback failed: %s", type(e).__name__)
            return RedirectResponse(_retour("error", parsed), status_code=302)
        return RedirectResponse(_retour("connected", parsed), status_code=302)

    return [Route(wp_auth.CALLBACK_PATH, callback, methods=["GET"])]
