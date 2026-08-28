"""Handlers des IMAGES de compte et d'organisation — avatar user, logo d'org.

- `POST|DELETE /api/me/avatar`      → avatar de l'utilisateur courant
- `POST|DELETE /api/orgs/{id}/logo` → logo UPLOADÉ de l'org (org_admin)

Upload multipart → ne passe PAS par la couche capacité (ADR 0009 = corps JSON
pydantic), d'où des routes écrites à la main. L'URL publique est persistée en clair
(ce n'est pas un secret). Le logo AFFICHÉ reste l'EFFECTIF (upload sinon dérivé
logo.dev du domaine déclaré) : `org_store.effective_logo_url`.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api.routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db, org_store
from .base import _authenticate, _json, _json_error


async def _read_upload(request: Request):
    """Parse un multipart, renvoie (data: bytes, err: JSONResponse|None)."""
    try:
        form = await request.form()
    except Exception:
        return None, _json_error(request, 400, "invalid_multipart")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return None, _json_error(request, 400, "missing_file")
    return await upload.read(), None


async def avatar_save(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    data, err = await _read_upload(request)
    if err:
        return err
    from .. import media_store
    try:
        url = media_store.upload_image("avatars", sub, data, "")
    except media_store.MediaError as e:
        return _json_error(request, e.status, e.code)
    old = (db.get_user(sub) or {}).get("avatar_url")
    db.set_avatar_url(sub, url)
    if old and old != url:
        media_store.delete_by_url(old)
    return _json(request, {"ok": True, "avatar_url": url})


async def avatar_clear(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    old = (db.get_user(sub) or {}).get("avatar_url")
    db.set_avatar_url(sub, None)
    if old:
        from .. import media_store
        media_store.delete_by_url(old)
    return _json(request, {"ok": True})


def _org_logo_gate(request: Request, sub: str):
    """Renvoie (org_id, err). 400 id invalide, 404 org inconnue, 403 non-admin."""
    from .. import roles
    try:
        org_id = int(request.path_params["id"])
    except (ValueError, KeyError):
        return None, _json_error(request, 400, "invalid_id")
    if not org_store.get_org(org_id):
        return None, _json_error(request, 404, "unknown_org")
    if not roles.is_org_admin(sub, org_id):
        return None, _json_error(request, 403, "forbidden")
    return org_id, None


async def org_logo_save(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    org_id, err = _org_logo_gate(request, sub)
    if err:
        return err
    data, err = await _read_upload(request)
    if err:
        return err
    from .. import media_store
    try:
        url = media_store.upload_image("org-logos", str(org_id), data, "")
    except media_store.MediaError as e:
        return _json_error(request, e.status, e.code)
    old = (org_store.get_org(org_id) or {}).get("logo_url")
    org_store.set_org_logo(org_id, url)
    if old and old != url:
        media_store.delete_by_url(old)
    return _json(request, {"ok": True, "logo_url": url})


async def org_logo_clear(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    org_id, err = _org_logo_gate(request, sub)
    if err:
        return err
    old = (org_store.get_org(org_id) or {}).get("logo_url")
    org_store.set_org_logo(org_id, None)
    if old:
        from .. import media_store
        media_store.delete_by_url(old)
    return _json(request, {"ok": True})
