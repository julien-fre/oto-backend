"""Retour du consentement Notion — la seule pièce écrite à la main.

Le `/start` n'est PAS ici : c'est le seam commun (`connectors/flow`), dont
`auth/notion._start_flow` est la déclaration. Seul le callback reste une route :
Notion y redirige un NAVIGATEUR, sans en-tête d'auth et avec un 302, ce qu'un
contrat de capacité ne peut pas exprimer. Même forme que `api/instagram_meta.py`.

L'identité vient du `state` signé, pas d'une session.
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
from ..auth import notion as notion_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

# Notion renvoie `error=access_denied` quand la personne annule l'écran de
# consentement — un refus, pas une panne.
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
            return_app, notion_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        erreur = request.query_params.get("error")
        parsed = notion_auth.verify_state(request.query_params.get("state"))
        if not parsed:
            logger.info("notion : retour de consentement sans state lisible")
            return RedirectResponse(_retour("error"), status_code=302)
        return_app, org_id = parsed["return_app"], parsed["org"]
        if erreur or not code:
            logger.info("notion : consentement non abouti (motif=%s)",
                        erreur or "code absent")
            return RedirectResponse(
                _retour("forbidden" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)

        def _echanger_et_ranger() -> str:
            tokens = notion_auth.exchange_code(code, parsed["app_key"])
            return notion_auth.persist(parsed["sub"], org_id, parsed["scope"],
                                       parsed["group"], parsed["app_key"], tokens)

        try:
            await run_in_threadpool(_echanger_et_ranger)   # DB + HTTP sync → hors boucle
        except Exception as e:  # noqa: BLE001
            # Jamais le détail dans l'URL ; le diagnostic va au journal, sans secret.
            logger.warning("notion : retour de consentement en échec (%s : %s)",
                           type(e).__name__, e)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(notion_auth._CALLBACK_PATH, callback, methods=["GET"])]
