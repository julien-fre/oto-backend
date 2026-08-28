"""Handlers des FICHIERS BRUTS d'un projet et de son export — carte « Autre
document » (ADR 0032 §3).

- `GET|POST /api/me/projects/{project_id}/files`               → liste / dépose
- `DELETE   /api/me/projects/{project_id}/files/{file_id}`      → supprime
- `POST     /api/me/projects/{project_id}/files/{file_id}/public` → bascule le partage
- `GET      /api/me/projects/{id}/export`                       → ZIP markdown de la KB

Upload multipart (PDF/HTML…) → hors couche capacité (corps binaire, pas JSON).
Blob DURABLE + privé en Object Storage ; accès par presigned à la lecture. Le
RESTE du domaine projet est déjà en capacités (`POST /api/me/projects` sert tout le
métier en `op=`) — ces quatre chemins-là ne le sont pas encore.

`_project_org_context_error` est le gate de CONTEXTE d'org (ADR 0023) de ces
routes par-id : le projet doit être visible dans l'org de CONSULTATION, pas
seulement accessible à l'acteur via une AUTRE de ses orgs.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api.routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import access, db, doc_export, ownership
from .base import _authenticate, _json, _json_error


def _project_org_context_error(request: Request, sub: str, pid: int):
    """Gate de CONTEXTE d'org (ADR 0023) des routes projet par-id : le projet doit être
    visible dans l'org de CONSULTATION (`access.current_org`), pas seulement accessible
    à l'acteur via une AUTRE de ses orgs (fuite cross-org — cf. l'incident projet). Le
    pendant REST du gate de la capacité `oto_project`. Renvoie une 404 non-disclosante
    si hors contexte, sinon None. Les routes d'ÉCRITURE gardent en plus leur check de
    permission `can_access(write)`."""
    from .. import ownership
    if ownership.visible_in_org(sub, access.current_org(sub), "project", str(pid)):
        return None
    return _json_error(request, 404, "unknown_project")


def _signed(row: dict) -> dict:
    from .. import media_store
    key = row.pop("s3_key", None)
    try:
        row["download_url"] = media_store.presign_get(key) if key else None
    except media_store.MediaError:
        row["download_url"] = None
    return row


async def project_files_list(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    pid = int(request.path_params["project_id"])
    if not db.get_project_by_id(pid):
        return _json_error(request, 404, "unknown_project")
    if (e := _project_org_context_error(request, sub, pid)):
        return e
    return _json(request, {"files": [_signed(r) for r in db.list_project_files(pid)]})


async def project_files_upload(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from .. import ownership, media_store
    pid = int(request.path_params["project_id"])
    if not db.get_project_by_id(pid):
        return _json_error(request, 404, "unknown_project")
    if (e := _project_org_context_error(request, sub, pid)):
        return e
    if not ownership.can_access(sub, "project", str(pid), "write"):
        return _json_error(request, 403, "forbidden")
    try:
        form = await request.form()
    except Exception:
        return _json_error(request, 400, "invalid_multipart")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return _json_error(request, 400, "missing_file")
    data = await upload.read()
    filename = getattr(upload, "filename", None) or "file"
    content_type = getattr(upload, "content_type", None) or "application/octet-stream"
    title = (str(form.get("title") or "")).strip() or None
    description = (str(form.get("description") or "")).strip() or None
    try:
        key = media_store.upload_object("project-files", str(pid), data, content_type, filename)
    except media_store.MediaError as e:
        return _json_error(request, e.status, e.code)
    row = db.add_project_file(pid, key, filename, mime=content_type,
                              size_bytes=len(data), title=title,
                              description=description, created_by=sub)
    db.log_project_activity(pid, sub, "project.file_add", title or filename)
    return _json(request, {"ok": True, "file": _signed(row)})


async def project_file_delete(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from .. import ownership, media_store
    pid = int(request.path_params["project_id"])
    file_id = int(request.path_params["file_id"])
    existing = db.get_project_file(file_id)
    if not existing or existing["project_id"] != pid:
        return _json_error(request, 404, "unknown_file")
    if (e := _project_org_context_error(request, sub, pid)):
        return e
    if not ownership.can_access(sub, "project", str(pid), "write"):
        return _json_error(request, 403, "forbidden")
    db.delete_project_file(file_id)
    media_store.delete_by_key(existing["s3_key"])
    db.log_project_activity(pid, sub, "project.file_delete",
                            existing.get("title") or existing.get("filename"))
    return _json(request, {"ok": True})


async def project_file_public(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Bascule le partage public d'un fichier (ADR 0032 §3, B4b) : ACL S3
    public-read ↔ private, URL publique permanente persistée."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from .. import ownership, media_store
    pid = int(request.path_params["project_id"])
    file_id = int(request.path_params["file_id"])
    existing = db.get_project_file(file_id)
    if not existing or existing["project_id"] != pid:
        return _json_error(request, 404, "unknown_file")
    if (e := _project_org_context_error(request, sub, pid)):
        return e
    if not ownership.can_access(sub, "project", str(pid), "write"):
        return _json_error(request, 403, "forbidden")
    try:
        body = await request.json()
    except Exception:
        return _json_error(request, 400, "invalid_json")
    make_public = bool(isinstance(body, dict) and body.get("public"))
    # La bascule S3 d'ABORD, la base ENSUITE : la ligne ne dit « public » ou « privé »
    # que si l'ACL a effectivement bougé. Les deux sens lèvent la même `MediaError`
    # (cf. `media_store.make_private`) — une ACL refusée est un refus rendu au client,
    # jamais un `{"ok": true}` sur un fichier resté ouvert.
    try:
        if make_public:
            public_url = media_store.make_public(existing["s3_key"])
        else:
            public_url = None
            media_store.make_private(existing["s3_key"])
    except media_store.MediaError as e:
        return _json_error(request, e.status, e.code)
    row = db.set_project_file_public(file_id, make_public, public_url)
    db.log_project_activity(pid, sub, "project.file_public",
                            f"{existing.get('title') or existing.get('filename')}:{make_public}")
    return _json(request, {"ok": True, "file": _signed(row)})


async def me_project_export(request: Request, *, verifier: JWTVerifier) -> Response:
    """Export d'un projet (KB) en ZIP d'arborescence markdown (oto/#6 B2 —
    réversibilité). Accès LECTURE requis. Les pages deviennent des .md ; une page
    à enfants → dossier + `_index.md`."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    try:
        pid = int(request.path_params["id"])
    except (KeyError, ValueError):
        return _json_error(request, 400, "bad_project")
    if not ownership.can_access(sub, "project", str(pid), "read"):
        return _json_error(request, 403, "forbidden")
    proj = db.get_project_by_id(pid) or {}
    docs = db.list_docs_for_project(pid)
    blob = await run_in_threadpool(doc_export.build_export, docs,
                                   doc_export._slug(proj.get("name") or "kb", pid))
    fname = f"{doc_export._slug(proj.get('name') or 'export', pid)}.zip"
    return Response(blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
