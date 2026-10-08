"""Return route of the Microsoft connections — the only hand-written piece.

Same shape as `api/meta_ads.py`: `/start` goes through the common seam
(`connectors/flow`), only the callback is a route (Microsoft redirects a
BROWSER, with no auth header). The identity comes from the signed `state`, whose
AUDIENCE says which return it is:

- `_retour_personne` — a person connected an account (or authorized a service): the
  code becomes a refresh token, in the vault;
- `_retour_approbation` — a client's administrator answered an approval link
  (`auth/microsoft.admin_consent_url`): NOTHING is exchanged nor written, the answer is
  only reported.

The return goes back to the card that asked (`state.c`), `?connector=<card>&connect=
<state>` (`auth/flow.connector_return_url`), in the vocabulary the dashboard reads:
`connected`, `error`, `forbidden` (the person refused), plus `admin_required` (the
person's organization forbids them to consent alone — the card offers the
administrator's approval) and `admin_approved` / `admin_refused` (an administrator's
answer to the approval link).
"""
from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable, Optional

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
_AADSTS = re.compile(r"AADSTS\d+")
#: The organization requires an administrator's approval (user consent disabled or
#: restricted; AADSTS65001 at exchange: consent missing for this application).
_ADMIN_REQUIS = ("AADSTS90094", "AADSTS90099", "AADSTS65001")


def _aadsts(texte: Optional[str]) -> Optional[str]:
    """The AADSTS code of an `error_description` — the only part we keep: the rest
    may name the organization."""
    m = _AADSTS.search(texte or "")
    return m.group(0) if m else None


def _verdict(erreur: Optional[str], aadsts: Optional[str],
             description: Optional[str] = None) -> str:
    """The return state of a connection that did not complete."""
    if aadsts in _ADMIN_REQUIS or "admin approval" in (description or "").lower():
        return "admin_required"
    return "forbidden" if erreur == _REFUS else "error"


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    def _retour(etat: str, return_app: str = "", connector: str = ms_auth.CONNECTOR,
                org_id: Optional[int] = None) -> Response:
        return RedirectResponse(oauth_flow.connector_return_url(
            return_app, connector, etat, org=org_id), status_code=302)

    def _retour_approbation(request: Request, etat: "ms_auth.EtatApprobation") -> Response:
        """An administrator's answer. No exchange, no write: each person still
        connects from the card — the approval only lets them."""
        q = request.query_params
        if q.get("error"):
            logger.info("microsoft: administrator approval not given (sub=%s, reason=%s)",
                        etat.sub, _aadsts(q.get("error_description")) or q.get("error"))
            return _retour("admin_refused", etat.app, etat.connector, etat.org)
        if (q.get("admin_consent") or "").lower() == "true":
            logger.info("microsoft: administrator approval given (sub=%s, services=%s)",
                        etat.sub, ",".join(etat.services))
            return _retour("admin_approved", etat.app, etat.connector, etat.org)
        return _retour("error", etat.app, etat.connector, etat.org)

    async def _retour_personne(request: Request, state: Optional[str]) -> Response:
        q = request.query_params
        etat = ms_auth.verify_state(state) if state else None
        if not etat:
            logger.info("microsoft: connection return without a readable state")
            return _retour("error")
        code, erreur = q.get("code"), q.get("error")
        if erreur or not code:
            aadsts = _aadsts(q.get("error_description"))
            # `error_description` may name the organisation: we only log codes.
            logger.info("microsoft: connection not completed (sub=%s, card=%s, reason=%s)",
                        etat.sub, etat.connector, aadsts or erreur or "missing code")
            return _retour(_verdict(erreur, aadsts, q.get("error_description")),
                           etat.app, etat.connector, etat.org)
        try:
            # Synchronous DB + HTTP off the event loop (oto-backend#867).
            await run_in_threadpool(ms_auth.finish_connection, etat, code)
        except Exception as e:
            # We log the traceback, never the `code` or the token.
            logger.exception("microsoft: connection return failed (sub=%s org=%s card=%s)",
                             etat.sub, etat.org, etat.connector)
            aadsts = getattr(e, "code", None)
            return _retour(_verdict(None, aadsts if isinstance(aadsts, str) else None),
                           etat.app, etat.connector, etat.org)
        return _retour("connected", etat.app, etat.connector, etat.org)

    async def callback(request: Request) -> Response:
        state = request.query_params.get("state")
        approbation = ms_auth.verify_admin_state(state) if state else None
        if approbation is not None:
            return _retour_approbation(request, approbation)
        return await _retour_personne(request, state)

    return [Route(ms_auth._CALLBACK_PATH, callback, methods=["GET"])]
