"""Ce qui RESTE de forme JSON dans les images de compte et les fichiers de projet.

Cinq routes écrites à la main jusqu'au 2026-08-27, portées en capacités (ADR 0009) —
mêmes chemins, mêmes codes, même corps sur le fil :

- `DELETE /api/me/avatar`                                  → efface mon avatar
- `DELETE /api/orgs/{id}/logo`                             → efface le logo UPLOADÉ de l'org
- `GET    /api/me/projects/{project_id}/files`             → liste les fichiers bruts
- `DELETE /api/me/projects/{project_id}/files/{file_id}`   → en supprime un
- `POST   /api/me/projects/{project_id}/files/{file_id}/public` → bascule son partage

⚠️ **Leurs quatre voisines ne migrent pas, et sont reclassées `NATURE`** dans
`tests/test_rest_modules_are_capabilities.py` : les trois `POST` multipart (avatar, logo
d'org, dépôt de fichier) et l'export ZIP d'un projet. L'adaptateur REST lit un corps
JSON et répond en JSON ; un corps binaire et une réponse `application/zip` sont hors du
moule par CONSTRUCTION, exactement comme `/api/upload/{token}`. On ne déforme pas
l'adaptateur pour quatre routes — on dit pourquoi elles restent dehors.

⚠️ **Duplication assumée avec `me.project_files`** (MCP `oto_project_files`), qui fait
déjà `list` et `delete` pour l'agent. Les RÉPONSES sont les mêmes ; ce qui diffère, ce
sont les REFUS (la face MCP joint un message à ses 404, la face REST rend le code nu) et
la forme d'entrée (`op` + `project_id` contre des paramètres de chemin). Fusionner les
deux changerait donc les corps servis au dashboard — hors du « mêmes réponses au
caractère près » de ce chantier. C'est la même famille de décision que la toolbox
(oto-backend#429), et elle est suivie à part.

**Pas de face MCP** ici : la lecture et la suppression sont déjà couvertes par
`oto_project_files`, et `oto_resource` porte le partage d'une ressource possédée. Ces
cinq-là sont les chemins du dashboard.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from .. import access, db, org_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_FILES = "/api/me/projects/{project_id:int}/files"
_FILE = _FILES + "/{file_id:int}"


# --- Entrées ----------------------------------------------------------------

class AvatarClearInput(BaseModel):
    """Aucun paramètre : l'avatar effacé est celui du porteur du jeton."""


class OrgLogoClearInput(BaseModel):
    # Texte, pas entier : la route rend `400 invalid_id`, pas le `invalid_input` de
    # pydantic. On convertit dans le handler pour garder le code servi.
    org_id: str = ""


class ProjectFilesListInput(BaseModel):
    project_id: int


class ProjectFileInput(BaseModel):
    project_id: int
    file_id: int


class ProjectFilePublicInput(BaseModel):
    project_id: int
    file_id: int
    # ⚠️ **REQUIS, et c'est un durcissement VOULU.** Le handler d'origine faisait
    # `bool(isinstance(body, dict) and body.get("public"))` : un corps sans `public`
    # valait donc « rendre privé », en silence. Combiné à l'adaptateur — qui avale un
    # corps illisible et le traite comme absent —, un JSON malformé aurait DÉPARTAGÉ le
    # fichier en rendant 200, là où la route rendait un 400 franc. On refuse plutôt que
    # d'agir : c'est la règle du dépôt, et ici l'action silencieuse était destructrice.
    public: bool


# --- Sorties ----------------------------------------------------------------

class Cleared(BaseModel):
    ok: bool


class ProjectFile(BaseModel):
    """⚠️ `s3_key` n'en fait PAS partie : la clé de stockage est retirée de chaque ligne
    et remplacée par `download_url`, une URL **signée et temporaire**. Elle expire — on
    ne la met pas en cache, on redemande la liste.

    `public_url` est l'autre régime : permanente, servie tant que le partage est ouvert.
    `null` = fichier privé."""
    id: int
    project_id: int
    filename: Optional[str] = None
    mime: Optional[str] = None
    size_bytes: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    # Résumé produit à l'ingestion, quand le format s'y prête.
    summary: Optional[str] = None
    public: Optional[bool] = None
    public_url: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[Any] = None
    # `null` quand le stockage n'a pas pu signer : la ligne reste servie, sans lien.
    download_url: Optional[str] = None


class ProjectFileList(BaseModel):
    files: list[ProjectFile]


class ProjectFileSaved(BaseModel):
    ok: bool
    file: ProjectFile


# --- Gardes partagées -------------------------------------------------------

def _org_context(ctx: ResolvedCtx, pid: int) -> None:
    """Gate de CONTEXTE d'org (ADR 0023) des routes projet par-id : le projet doit être
    visible dans l'org de CONSULTATION, pas seulement accessible à l'acteur via une
    AUTRE de ses orgs (fuite cross-org). 404 non-disclosante."""
    from .. import ownership
    if not ownership.visible_in_org(ctx.sub, access.current_org(ctx.sub), "project", str(pid)):
        raise AuthzDenied(404, "unknown_project")


def _signed(row: dict) -> dict:
    from .. import media_store
    key = row.pop("s3_key", None)
    try:
        row["download_url"] = media_store.presign_get(key) if key else None
    except media_store.MediaError:
        row["download_url"] = None
    return row


def _fichier(ctx: ResolvedCtx, pid: int, file_id: int) -> dict:
    """La ligne du fichier, après les trois gates d'écriture, dans l'ORDRE servi :
    existence + appartenance au projet (404), contexte d'org (404), permission (403)."""
    from .. import ownership
    existing = db.get_project_file(file_id)
    if not existing or existing["project_id"] != pid:
        raise AuthzDenied(404, "unknown_file")
    _org_context(ctx, pid)
    if not ownership.can_access(ctx.sub, "project", str(pid), "write"):
        raise AuthzDenied(403, "forbidden")
    return existing


# --- Handlers ---------------------------------------------------------------

def _avatar_clear(ctx: ResolvedCtx, inp: AvatarClearInput) -> dict:
    old = (db.get_user(ctx.sub) or {}).get("avatar_url")
    db.set_avatar_url(ctx.sub, None)
    if old:
        from .. import media_store
        media_store.delete_by_url(old)
    return {"ok": True}


def _org_logo_clear(ctx: ResolvedCtx, inp: OrgLogoClearInput) -> dict:
    """⚠️ Ordre des refus PRÉSERVÉ : id illisible (400) → org inconnue (404) → non-admin
    (403). Une règle d'autz déclarée (`ORG_ADMIN_OF`) rendrait 403 sur une org inconnue,
    là où cette route rend 404 depuis toujours."""
    from .. import roles
    try:
        org_id = int(inp.org_id)
    except (TypeError, ValueError):
        raise AuthzDenied(400, "invalid_id")
    if not org_store.get_org(org_id):
        raise AuthzDenied(404, "unknown_org")
    if not roles.is_org_admin(ctx.sub, org_id):
        raise AuthzDenied(403, "forbidden")
    old = (org_store.get_org(org_id) or {}).get("logo_url")
    org_store.set_org_logo(org_id, None)
    if old:
        from .. import media_store
        media_store.delete_by_url(old)
    return {"ok": True}


def _files_list(ctx: ResolvedCtx, inp: ProjectFilesListInput) -> dict:
    if not db.get_project_by_id(inp.project_id):
        raise AuthzDenied(404, "unknown_project")
    _org_context(ctx, inp.project_id)
    return {"files": [_signed(r) for r in db.list_project_files(inp.project_id)]}


def _file_delete(ctx: ResolvedCtx, inp: ProjectFileInput) -> dict:
    from .. import media_store
    existing = _fichier(ctx, inp.project_id, inp.file_id)
    db.delete_project_file(inp.file_id)
    media_store.delete_by_key(existing["s3_key"])
    db.log_project_activity(inp.project_id, ctx.sub, "project.file_delete",
                            existing.get("title") or existing.get("filename"))
    return {"ok": True}


def _file_public(ctx: ResolvedCtx, inp: ProjectFilePublicInput) -> dict:
    """Bascule le partage public d'un fichier (ADR 0032 §3, B4b) : ACL S3 public-read ↔
    private, URL publique permanente persistée."""
    from .. import media_store
    existing = _fichier(ctx, inp.project_id, inp.file_id)
    make_public = bool(inp.public)
    try:
        public_url = media_store.make_public(existing["s3_key"]) if make_public else None
    except media_store.MediaError as e:
        raise AuthzDenied(e.status, e.code)
    if not make_public:
        media_store.make_private(existing["s3_key"])
    row = db.set_project_file_public(inp.file_id, make_public, public_url)
    db.log_project_activity(inp.project_id, ctx.sub, "project.file_public",
                            f"{existing.get('title') or existing.get('filename')}:{make_public}")
    return {"ok": True, "file": _signed(row)}


_D_AVATAR = ("Efface mon avatar. L'objet stocké est supprimé avec la référence — pas "
             "d'orphelin dans le stockage.")
_D_LOGO = ("Efface le logo UPLOADÉ de l'org (org_admin). ⚠️ Le logo AFFICHÉ ne disparaît "
           "pas forcément : il retombe sur celui dérivé du domaine de marque déclaré. "
           "C'est l'upload qu'on retire, pas l'affichage.")
_D_LIST = ("Les fichiers bruts attachés à un projet (« Autre document »). Chaque ligne "
           "porte une `download_url` **signée et temporaire** — elle expire, on ne la "
           "met pas en cache. `public_url`, elle, est permanente tant que le partage est "
           "ouvert. Lecture bornée à l'org de consultation : un projet visible via une "
           "AUTRE de mes orgs rend 404.")
_D_DELETE = ("Supprime un fichier d'un projet (accès écriture). L'objet stocké part avec "
             "la ligne.")
_D_PUBLIC = ("Ouvre ou ferme le partage public d'un fichier. `public` est REQUIS : un "
             "corps sans lui est refusé plutôt que traité comme « rendre privé », parce "
             "qu'un corps mal formé ne doit pas départager un fichier en silence.")

CAPABILITIES += [
    Capability(
        key="me.avatar.clear", handler=_avatar_clear, Input=AvatarClearInput,
        authz=SUB_ONLY, Output=Cleared, description=_D_AVATAR, mcp=None,
        rest=RestBinding("DELETE", "/api/me/avatar"),
    ),
    Capability(
        key="org.logo.clear", handler=_org_logo_clear, Input=OrgLogoClearInput,
        authz=SUB_ONLY, Output=Cleared, description=_D_LOGO, mcp=None,
        rest=RestBinding("DELETE", "/api/orgs/{id}/logo", path_map={"id": "org_id"}),
    ),
    Capability(
        key="me.project_file.list", handler=_files_list, Input=ProjectFilesListInput,
        authz=SUB_ONLY, Output=ProjectFileList, description=_D_LIST, mcp=None,
        rest=RestBinding("GET", _FILES),
    ),
    Capability(
        key="me.project_file.delete", handler=_file_delete, Input=ProjectFileInput,
        authz=SUB_ONLY, Output=Cleared, description=_D_DELETE, mcp=None,
        rest=RestBinding("DELETE", _FILE),
    ),
    Capability(
        key="me.project_file.set_public", handler=_file_public,
        Input=ProjectFilePublicInput, authz=SUB_ONLY, Output=ProjectFileSaved,
        description=_D_PUBLIC, mcp=None,
        rest=RestBinding("POST", _FILE + "/public"),
    ),
]
