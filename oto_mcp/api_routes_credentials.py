"""Handlers de la connexion par SESSION NAVIGATEUR (Live View Browserbase).

- `POST /api/me/connectors/{name}/session/{start,finalize}`

Le geste produit le même objet qu'un formulaire de credential — une ligne du coffre
scopée `(sub, org)` (ADR 0033) — mais par un login humain dans un navigateur hébergé,
là où l'autre voie dérive un formulaire du schéma du connecteur. Cette autre voie est
passée en capacité le 2026-08-27 (`capabilities/me_credentials.py`,
`GET|POST|DELETE /api/settings/api-keys/{provider}`) : une capacité peut être REST-only
(binding `mcp` retiré) — poser un secret reste dashboard-only par design, il ne passe
jamais en argument d'outil.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

import asyncio

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import access, connectors
from .api_routes_base import _authenticate, _json, _json_error


async def session_start(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from . import browser_session
    name = request.path_params["name"]
    if not browser_session.is_session_connector(name):
        return _json_error(request, 404, "not_a_session_connector")
    # Connecteur GÉNÉRIQUE (`browser`, oto-private#79) : le SITE vient de l'appel —
    # `?url=` ouvre la Live View sur la page de connexion demandée. Absent (les
    # connecteurs à site unique) ⇒ la `login_url` enregistrée, comportement inchangé.
    url = (request.query_params.get("url") or "").strip() or None
    try:
        out = await asyncio.to_thread(
            lambda: browser_session.start(sub, name, login_url=url))
    except browser_session.SessionError as e:
        return _json_error(request, 503, "browserbase_unavailable", str(e))
    return _json(request, out)


async def session_finalize(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from . import browser_session
    name = request.path_params["name"]
    if not browser_session.is_session_connector(name):
        return _json_error(request, 404, "not_a_session_connector")
    try:
        body = await request.json()
    except Exception:
        return _json_error(request, 400, "invalid_json")
    context_id = (body or {}).get("context_id")
    session_id = (body or {}).get("session_id")
    if not context_id or not session_id:
        return _json_error(request, 400, "missing_params")
    # Niveau de configuration de l'instance (ADR 0038/0044) : member (défaut, ma
    # session perso), org (partagée à toute l'org), group (partagée à l'équipe).
    # Les niveaux partagés exigent d'être admin du scope + connecteur org-partageable.
    scope = ((body or {}).get("scope") or "member").strip()
    if scope not in ("member", "org", "group"):
        return _json_error(request, 400, "invalid_scope")
    from . import roles
    group_id = None
    if scope in ("org", "group"):
        org_id = access.current_org(sub)
        if org_id is None:
            return _json_error(request, 400, "no_org_context")
        if not connectors.is_org_shareable(name):
            return _json_error(request, 400, "not_org_shareable")
        if scope == "org":
            if not roles.is_org_admin(sub, org_id):
                return _json_error(request, 403, "forbidden")
        else:
            group_id = access.current_group(sub)
            if group_id is None:
                return _json_error(request, 400, "no_group_context")
            if not roles.can_admin_group(sub, group_id):
                return _json_error(request, 403, "forbidden")
    # Compte du coffre visé — connecteur générique : le site (host). `force` =
    # persister sans la vérification générique de login (refusé par le seam pour
    # un connecteur à site unique, dont le verify est une vraie sonde d'API).
    account = ((body or {}).get("account") or "").strip()
    force = bool((body or {}).get("force"))
    try:
        connected = await browser_session.finalize(
            sub, name, context_id, session_id, scope=scope, group_id=group_id,
            account=account, force=force)
    except browser_session.SessionError as e:
        return _json_error(request, 502, "session_verify_failed", str(e))
    return _json(request, {"connected": connected, "scope": scope,
                           "account": account})
