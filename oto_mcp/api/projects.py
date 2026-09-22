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
from .base import _authenticate, _file, _json, _json_error


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


async def project_files_upload(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from .. import ownership, media_store, upload_tokens
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
        # Le plafond d'un FICHIER DE PROJET, le même que le dépôt par lien signé : sans
        # lui, `upload_object` retombe sur celui d'une image (2 Mo).
        key = media_store.upload_object("project-files", str(pid), data, content_type,
                                        filename, max_bytes=upload_tokens.max_bytes())
    except media_store.MediaError as e:
        return _json_error(request, e.status, e.code)
    row = db.add_project_file(pid, key, filename, mime=content_type,
                              size_bytes=len(data), title=title,
                              description=description, created_by=sub)
    db.log_project_activity(pid, sub, "project.file_add", title or filename)
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
    # Le gate de CONTEXTE, comme les quatre autres routes par-id de ce module. Il
    # manquait ici : `can_access` seul est l'union de TOUTES les orgs de l'acteur, si
    # bien qu'un projet d'une AUTRE de mes orgs s'exportait depuis un contexte où
    # `GET .../files` rendait déjà 404. L'acteur y avait bien droit — ce n'est pas une
    # fuite entre organisations — mais la bascule d'org ne bornait pas l'export, et
    # une route par-id qui échappe au gate est celle par laquelle l'écart revient.
    if (e := _project_org_context_error(request, sub, pid)):
        return e
    if not ownership.can_access(sub, "project", str(pid), "read"):
        return _json_error(request, 403, "forbidden")
    proj = db.get_project_by_id(pid) or {}
    docs = db.list_docs_for_project(pid)
    blob = await run_in_threadpool(doc_export.build_export, docs,
                                   doc_export._slug(proj.get("name") or "kb", pid))
    fname = f"{doc_export._slug(proj.get('name') or 'export', pid)}.zip"
    # `_file` : même raison que le PDF de facture — une `Response` nue sort SANS
    # CORS, et le ZIP n'arrive jamais au navigateur qui l'a demandé.
    return _file(request, blob, media_type="application/zip", filename=fname)
