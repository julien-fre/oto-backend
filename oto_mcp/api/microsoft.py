"""Route de retour de la connexion Microsoft — la seule pièce écrite à la main.

Même forme qu'`api/meta_ads.py` : le `/start` passe par le seam commun
(`connectors/flow`), seul le callback est une route (Microsoft redirige un
NAVIGATEUR, sans en-tête d'auth). L'identité vient du `state` signé.

⚠️ `error=access_denied` couvre aussi bien un refus de la personne qu'une
organisation qui exige le consentement d'un administrateur : la fiche dit
« refusé », et le texte du connecteur explique le second cas.
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
from ..auth import microsoft as ms_auth

logger = logging.getLogger(__name__)

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

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
            return_app, ms_auth.CONNECTOR, etat, org=org_id)

    async def callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        erreur = request.query_params.get("error")
        parsed = ms_auth.verify_state(state) if state else None
        if not parsed:
            logger.info("sharepoint : retour de connexion sans state lisible")
            return RedirectResponse(_retour("error"), status_code=302)
        sub, org_id, return_app = parsed
        if erreur or not code:
            # `error_description` peut nommer l'organisation : on ne journalise que
            # le code d'erreur.
            logger.info("sharepoint : connexion non aboutie (sub=%s, motif=%s)",
                        sub, erreur or "code absent")
            return RedirectResponse(
                _retour("forbidden" if erreur == _REFUS else "error",
                        return_app, org_id), status_code=302)

        def _echanger_et_ranger() -> dict:
            coordonnees = ms_auth.app()
            grant = ms_auth._coeur().auth.exchange_code(
                coordonnees["client_id"], coordonnees["client_secret"], code,
                oauth_flow.redirect_uri(ms_auth._CALLBACK_PATH))
            return ms_auth.persist_grant(sub, org_id, grant)

        try:
            # DB + HTTP synchrones hors de la boucle (oto-backend#867).
            await run_in_threadpool(_echanger_et_ranger)
        except Exception:
            # On journalise le traceback, jamais le `code` ni le jeton.
            logger.exception("sharepoint : retour de connexion en échec "
                             "(sub=%s org=%s)", sub, org_id)
            return RedirectResponse(_retour("error", return_app, org_id),
                                    status_code=302)
        return RedirectResponse(_retour("connected", return_app, org_id),
                                status_code=302)

    return [Route(ms_auth._CALLBACK_PATH, callback, methods=["GET"])]
