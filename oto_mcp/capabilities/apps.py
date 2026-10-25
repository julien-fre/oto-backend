"""Apps : écrans bâtis par les utilisateurs, rangés dans un projet (`oto_apps`,
`POST /api/me/apps`).

**Une app n'est pas une page de mémoire.** Jusqu'ici une app vivait dans une page
(un bloc de code dans un `oto_doc`) : elle polluait la mémoire du projet et la
recherche, la liste des apps devait les DEVINER par recherche plein texte, et le
serveur ne savait ni la valider ni dire qui l'avait écrite. Elle a désormais sa table
(`db/schema/apps.py`), ses révisions et cette capacité — `docs/apps.md`.

**L'accès est celui du projet** (`ownership.can_access`, lecture / écriture), comme
une page. Une app inconnue OU illisible rend le même 404 : rien ne dit qu'elle existe.

**Le manifeste se juge à l'écriture** (`apps_manifeste.py`) : sa forme, et que chaque
tableau, champ et agent déclarés sont à la portée de l'AUTEUR. Un manifeste invalide
est refusé en 400 `invalid_manifest` avec la liste de ce qui ne va pas ; `op=validate`
rend cette liste sans rien écrire.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .. import db, ownership
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .apps_manifeste import auteur, forme
from .registry import CAPABILITIES

PROJECT_RTYPE = "project"
MAX_HTML = 512_000
MAX_TITLE = 200
MAX_PROJECTS = 200
_THUMB_KEYS = {"image", "color", "emoji", "design"}


class AppInput(BaseModel):
    op: Literal["list", "get", "create", "update", "archive", "unarchive",
                "revisions", "restore", "validate"]
    project_id: Optional[int] = None
    project_ids: Optional[list[int]] = Field(
        default=None, description="list: the projects to list apps from (at most 200).")
    app_id: Optional[int] = None
    title: Optional[str] = None
    manifest: Optional[dict] = None
    html: Optional[str] = None
    thumbnail: Optional[dict] = None
    clear_thumbnail: Optional[bool] = None
    expected_rev: Optional[str] = None
    revision_id: Optional[int] = None
    archived: Optional[bool] = None


class AppCard(BaseModel):
    id: int
    project_id: int
    title: str
    thumbnail: Optional[dict] = None
    created_by: Optional[str] = None
    created_at: Optional[Any] = None
    updated_by: Optional[str] = None
    updated_at: Optional[Any] = None
    archived_at: Optional[Any] = None


class AppView(AppCard):
    manifest: dict
    html: str
    rev: str


class AppRevision(BaseModel):
    id: int
    title: str
    edited_by: Optional[str] = None
    created_at: Optional[Any] = None


class AppOut(BaseModel):
    app: Optional[AppView] = None
    apps: Optional[list[AppCard]] = None
    revisions: Optional[list[AppRevision]] = None
    # validate : vide = le manifeste passe.
    problems: Optional[list[str]] = None
    ok: Optional[bool] = None


def _require(cond, code: str, msg: str, status: int = 400) -> None:
    if not cond:
        raise AuthzDenied(status, code, msg)


def _can(sub: str, project_id: int, want: str) -> bool:
    return ownership.can_access(sub, PROJECT_RTYPE, str(project_id), want)


def _app_or_404(sub: str, app_id: Optional[int], want: str) -> dict:
    _require(app_id is not None, "missing_app_id", "`app_id` is required for this op.")
    row = db.apps.get(int(app_id))
    # Inconnue ou illisible : la même réponse, rien ne dit qu'elle existe.
    _require(row is not None and _can(sub, row["project_id"], "read"),
             "unknown_app", f"App {app_id} doesn't exist or isn't shared with you.", 404)
    if want == "write":
        _require(_can(sub, row["project_id"], "write"), "forbidden",
                 "You can read this app but not change it.", 403)
    return row


def _thumbnail(raw: Optional[dict]) -> Optional[dict]:
    if raw is None:
        return None
    _require(isinstance(raw, dict) and set(raw) <= _THUMB_KEYS, "invalid_thumbnail",
             f"A thumbnail takes only: {', '.join(sorted(_THUMB_KEYS))}.")
    image = raw.get("image")
    _require(image is None or (isinstance(image, str) and image.startswith("https://")
                               and len(image) <= 2000),
             "invalid_thumbnail", "`thumbnail.image` must be an https address.")
    for k, cap in (("color", 24), ("emoji", 16)):
        v = raw.get(k)
        _require(v is None or (isinstance(v, str) and len(v) <= cap), "invalid_thumbnail",
                 f"`thumbnail.{k}` is too long.")
    _require(raw.get("design") is None or isinstance(raw.get("design"), dict),
             "invalid_thumbnail", "`thumbnail.design` must be an object.")
    return {k: v for k, v in raw.items() if v is not None}


def _checked_manifest(ctx: ResolvedCtx, raw: Optional[dict]) -> dict:
    clean, problems = forme(raw if raw is not None else {})
    if not problems:
        problems = auteur(ctx.sub, ctx.org_id, clean)
    if problems:
        raise AuthzDenied(400, "invalid_manifest",
                          "The manifest isn't valid: " + " ".join(problems))
    return clean


def _content(inp: AppInput, *, creating: bool) -> None:
    if creating or inp.title is not None:
        _require(isinstance(inp.title, str) and inp.title.strip() and len(inp.title) <= MAX_TITLE,
                 "invalid_title", f"A title is 1 to {MAX_TITLE} characters.")
    if inp.html is not None:
        _require(len(inp.html) <= MAX_HTML, "html_too_large",
                 f"An app's HTML is at most {MAX_HTML} characters.")


def _view(row: dict) -> dict:
    return {k: row[k] for k in AppView.model_fields if k in row}


def _app(ctx: ResolvedCtx, inp: AppInput) -> dict:
    sub = ctx.sub

    if inp.op == "list":
        ids = list(inp.project_ids or ([] if inp.project_id is None else [inp.project_id]))
        _require(ids, "missing_project", "`project_ids` (or `project_id`) is required to list apps.")
        _require(len(ids) <= MAX_PROJECTS, "too_many_projects",
                 f"At most {MAX_PROJECTS} projects per list.")
        owners = db.apps.project_owners(ids)
        readable = ownership.can_access_many(sub, PROJECT_RTYPE, owners, "read") if owners else {}
        allowed = [int(pid) for pid, ok in readable.items() if ok]
        return {"apps": db.apps.list_for_projects(allowed, archived=inp.archived is True)}

    if inp.op == "validate":
        clean, problems = forme(inp.manifest if inp.manifest is not None else {})
        if not problems:
            problems = auteur(sub, ctx.org_id, clean)
        return {"ok": not problems, "problems": problems}

    if inp.op == "create":
        _require(inp.project_id is not None, "missing_project", "`project_id` is required.")
        _require(_can(sub, int(inp.project_id), "write"), "forbidden",
                 "You can't add an app to this project.", 403)
        _content(inp, creating=True)
        manifest = _checked_manifest(ctx, inp.manifest)
        row = db.apps.create(int(inp.project_id), title=inp.title.strip(), manifest=manifest,
                             html=inp.html or "", thumbnail=_thumbnail(inp.thumbnail), by=sub)
        db.log_project_activity(int(inp.project_id), sub, "app.create", row["title"])
        return {"app": _view(row)}

    row = _app_or_404(sub, inp.app_id, "read" if inp.op in ("get", "revisions") else "write")

    if inp.op == "get":
        return {"app": _view(row)}

    if inp.op == "revisions":
        return {"revisions": db.apps.revisions(row["id"])}

    if inp.op in ("archive", "unarchive"):
        out = db.apps.set_archived(row["id"], inp.op == "archive")
        db.log_project_activity(row["project_id"], sub, f"app.{inp.op}", row["title"])
        return {"app": _view(out)}

    if inp.op == "restore":
        _require(inp.revision_id is not None, "missing_revision", "`revision_id` is required.")
        old = db.apps.revision(row["id"], int(inp.revision_id))
        _require(old is not None, "unknown_revision", "No such revision of this app.", 404)
        changed = _update(sub, row, inp.expected_rev, title=old["title"],
                          manifest=old["manifest"], html=old["html"], thumbnail=old["thumbnail"])
        db.log_project_activity(row["project_id"], sub, "app.restore", row["title"])
        return {"app": _view(changed)}

    # update
    _content(inp, creating=False)
    manifest = _checked_manifest(ctx, inp.manifest) if inp.manifest is not None else None
    thumb: Any = ...
    if inp.clear_thumbnail:
        thumb = None
    elif inp.thumbnail is not None:
        thumb = _thumbnail(inp.thumbnail)
    changed = _update(sub, row, inp.expected_rev, title=inp.title.strip() if inp.title else None,
                      manifest=manifest, html=inp.html, thumbnail=thumb)
    db.log_project_activity(row["project_id"], sub, "app.update", changed["title"])
    return {"app": _view(changed)}


def _update(sub: str, row: dict, expected_rev: Optional[str], **fields) -> dict:
    try:
        out = db.apps.update(row["id"], by=sub, expected_rev=expected_rev, **fields)
    except db.apps.AppConflict as e:
        raise AuthzDenied(409, "conflict",
                          f"This app changed since you read it (now at rev {e.current_rev}): "
                          "read it again before writing.") from None
    _require(out is not None, "unknown_app", f"App {row['id']} doesn't exist.", 404)
    return out


CAPABILITIES += [
    Capability(
        key="me.app", handler=_app, Input=AppInput, Output=AppOut, authz=SUB_ONLY,
        description=(
            "Apps: screens people build on Tulina — dashboards, review queues, forms — "
            "kept in a project and opened with the project's access. An app is a "
            "manifest (the tables and fields it reads, the field values it may set, "
            "the columns a form may add, the agents it may read or start with "
            "`run: true`, `files: true` to upload) plus HTML that runs sandboxed and "
            "talks to Tulina through `window.tulina`. Not a memory page.\n"
            "op=list (`project_ids`; cards, no HTML) / get (`app_id`; with manifest, "
            "HTML and `rev`) / create (`project_id`, `title`, `manifest`, `html`, "
            "optional `thumbnail`) / update (`app_id`, any of those; pass "
            "`expected_rev` from get → 409 if it changed) / archive / unarchive / "
            "revisions / restore (`revision_id`) / validate (`manifest`: the problems "
            "a create would refuse, nothing written). Every declared table, field and "
            "agent must be within YOUR reach: a create or update naming one you can't "
            "read is refused (400 invalid_manifest, each problem listed)."
        ),
        mcp="oto_apps",
        rest=RestBinding("POST", "/api/me/apps"),
    ),
]
