"""Handlers du PALIER ADMIN encore écrits à la main — clés plateforme et jetons
émis pour un tiers.

- `GET|POST /api/admin/platform-keys` + `DELETE …/{provider}/{label}`
      → instances de scope PLATFORM du coffre unifié (ADR 0044 §F, fin de
        `platform_keys`). Le secret n'est JAMAIS déchiffré ni renvoyé : identité
        seule (provider, label, set_at).
- `GET|POST /api/admin/users/{sub}/tokens` + `DELETE …/{token_id}`
      → jetons API émis POUR UN SUB TIERS.

⚠️ Les routes `tokens` portent `allow_api_token=False` — un jeton ne fabrique pas
de jeton, sinon une fuite s'auto-entretient (l'attaquant s'émet un second jeton
non-expirant avant qu'on révoque le premier). Ici l'enjeu est pire qu'au palier
membre : ces routes émettent pour quelqu'un d'autre. C'est aussi le cran que
`_rest_adapter` ne sait pas encore exprimer, donc la raison pour laquelle ces
quatre chemins ne sont pas encore des capacités.

`/api/admin/*` est retiré du descriptif OpenAPI public : une console de plateforme
n'a pas d'intégrateur tiers.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import access, credentials_store, db, token_scopes
from .api_routes_base import _authenticate, _json, _json_error
from .json_body import InvalidJsonBody, read_json_body


async def admin_platform_keys_list(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    # ADR 0044 §F : instances scope PLATFORM du coffre unifié (plus platform_keys). Le
    # secret n'est JAMAIS déchiffré/renvoyé — identité (provider, label, set_at) seulement.
    return _json(request, {"platform_keys": credentials_store.list_platform_credentials()})


async def admin_platform_key_create(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    try:
        body = await request.json()
    except Exception:
        return _json_error(request, 400, "invalid_json")
    if not isinstance(body, dict):
        return _json_error(request, 400, "invalid_body")
    provider = (body.get("provider") or "").strip()
    label = (body.get("label") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    if provider not in db.KEY_PROVIDERS:
        return _json_error(request, 400, "invalid_provider")
    if not label or not api_key:
        return _json_error(request, 400, "missing_fields")
    # ADR 0044 §F : la clé plateforme est une instance scope PLATFORM du coffre unifié
    # (fin de platform_keys).
    try:
        credentials_store.set_credential(credentials_store.PLATFORM, label, provider,
                                         api_key, set_by=sub)
    except ValueError as e:
        return _json_error(request, 400, "invalid_platform_provider", str(e))
    return _json(request, {"provider": provider, "label": label})


async def admin_platform_key_delete(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    provider = (request.path_params.get("provider") or "").strip()
    label = (request.path_params.get("label") or "").strip()
    # ADR 0044 §F : supprime l'instance plateforme (ses grants vivent sur sa ligne
    # share_down/meta → partent avec elle, pas d'orphelin).
    if not credentials_store.clear_credential(credentials_store.PLATFORM, label, provider):
        return _json_error(request, 404, "unknown_key")
    return _json(request, {"ok": True, "provider": provider, "label": label})


# Gestion des jetons (palier admin) : `allow_api_token=False` — même règle que
# `/api/me/tokens`, un jeton ne fabrique pas de jeton. Ici l'enjeu est pire :
# ces routes émettent pour un sub TIERS.
async def admin_tokens_list(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier, allow_api_token=False)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    target_sub = request.path_params["sub"]
    if not db.get_user(target_sub):
        return _json_error(request, 404, "unknown_user")
    return _json(request, {"tokens": db.list_api_tokens(target_sub)})


async def admin_tokens_create(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier, allow_api_token=False)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    target_sub = request.path_params["sub"]
    if not db.get_user(target_sub):
        return _json_error(request, 404, "unknown_user")
    # Corps illisible ⇒ REFUS, jamais un jeton. Même défaut qu'en `me_tokens_create`
    # (site B2), en pire : le jeton émis pour un TIERS était non porté **et sans
    # expiration** — `ttl_days` retombait sur None avec le reste
    # (`docs/silences-2026-08-27.md`, site B3).
    try:
        body = await read_json_body(request)
    except InvalidJsonBody as e:
        return _json_error(request, 400, e.code, e.detail)
    label = body.get("label") or "cli"
    ttl_raw = body.get("ttl_days")
    ttl_days = int(ttl_raw) if isinstance(ttl_raw, (int, str)) and str(ttl_raw).isdigit() else None
    try:
        scopes = token_scopes.parse(body.get("scopes"))
    except token_scopes.ScopeError as e:
        return _json_error(request, 400, "invalid_scopes", str(e))
    token = db.create_api_token(target_sub, label=label.strip()[:32],
                                ttl_days=ttl_days, scopes=scopes)
    return _json(request, {"token": token, "label": label, "ttl_days": ttl_days,
                           "scopes": scopes})


async def admin_tokens_delete(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier, allow_api_token=False)
    if err:
        return err
    if not access.is_super_admin(sub):
        return _json_error(request, 403, "forbidden")
    target_sub = request.path_params["sub"]
    try:
        token_id = int(request.path_params["token_id"])
    except ValueError:
        return _json_error(request, 400, "invalid_id")
    ok = db.delete_api_token(target_sub, token_id)
    if not ok:
        return _json_error(request, 404, "unknown_token")
    return _json(request, {"ok": True, "id": token_id})
