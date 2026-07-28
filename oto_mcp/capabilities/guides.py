"""Guides ON-DEMAND côté REST (ADR 0042) — surface dashboard des how-to.

Miroir REST de l'outil MCP `oto_guide` (tools/guide.py) : même cœur `guide_store`,
thin adapter d'autz par SCOPE. Le dashboard (REST-only) peut ainsi lister / lire /
rédiger / supprimer les guides on-demand PLATEFORME (platform_admin), d'ORG (org_admin)
et PERSO (self) — tout-DB 2026-07-16, les fichiers `guides/*.md` = seeds de boot.

Distinct des readmes INIT (delivery='init', injectés au handshake — édités par
`me.agent_readme` / `platform.instructions`) et des PROCÉDURES (org_instructions, slots).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import guide_store
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_MAX_BODY_BYTES = 64 * 1024


class _NoInput(BaseModel):
    pass


class GuideRefInput(BaseModel):
    scope: str
    slug: str


class GuideSetInput(BaseModel):
    scope: str
    slug: str
    body_md: str = ""
    title: str = ""
    description: str = ""


class GuideOpInput(BaseModel):
    """Face MCP op-aware (`oto_guide`). `scope` défaut 'user' à l'écriture — un agent
    qui rédige sans préciser écrit POUR SON utilisateur, jamais pour l'org/la plateforme."""
    op: Literal["list", "read", "write", "delete"] = "list"
    slug: Optional[str] = None
    scope: Optional[str] = None
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None


def _owner_for_write(ctx: ResolvedCtx, scope: str) -> str:
    """Le owner_id d'écriture pour `scope`, avec autz. platform → platform_admin ;
    org → org_admin de l'org active ; user → self. Lève AuthzDenied."""
    from .. import roles
    if scope == "user":
        return ctx.sub
    if scope == "org":
        if ctx.org_id is None:
            raise AuthzDenied(400, "no_active_org", "Aucune org active — vois `oto_use_org`.")
        if not roles.is_org_admin(ctx.sub, ctx.org_id):
            raise AuthzDenied(403, "forbidden", "Réservé à un admin de l'org (guide d'org).")
        return str(ctx.org_id)
    if scope == "platform":
        if not roles.is_platform_admin(ctx.sub):
            raise AuthzDenied(403, "forbidden", "Réservé à l'admin plateforme (guide plateforme).")
        return guide_store.PLATFORM_OWNER
    raise AuthzDenied(400, "bad_scope", "scope éditable = platform | org | user.")


def _list(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    """Catalogue des guides on-demand visibles : plateforme ∪ org active ∪ perso (DB)."""
    return {"guides": guide_store.list_guides_for(ctx.sub, ctx.org_id)}


def _get(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    # `scope` vide = pas de filtre : le store cherche plateforme → org → user (1er match).
    g = guide_store.read_guide_scoped(inp.slug, scope=inp.scope or None,
                                      org_id=ctx.org_id, sub=ctx.sub)
    if g is None:
        where = f" (scope {inp.scope})" if inp.scope else ""
        raise AuthzDenied(404, "not_found",
                          f"Guide `{inp.slug}`{where} introuvable — liste-les avec op=list.")
    return g


def _set(ctx: ResolvedCtx, inp: GuideSetInput) -> dict:
    owner_id = _owner_for_write(ctx, inp.scope)
    if not (inp.body_md or "").strip():
        raise AuthzDenied(400, "missing_body", "`body_md` requis.")
    if len(inp.body_md.encode()) > _MAX_BODY_BYTES:
        raise AuthzDenied(400, "body_too_large", "`body_md` > 64 KB.")
    try:
        return guide_store.set_guide(inp.scope, owner_id, inp.slug, inp.body_md,
                                     inp.title or "", inp.description or "")
    except guide_store.GuideError as e:
        raise AuthzDenied(400, "invalid_guide", str(e))


def _delete(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    owner_id = _owner_for_write(ctx, inp.scope)
    deleted = guide_store.delete_guide(inp.scope, owner_id, inp.slug)
    if not deleted:
        raise AuthzDenied(404, "not_found", f"Guide `{inp.slug}` (scope {inp.scope}) absent.")
    return {"scope": inp.scope, "slug": inp.slug, "deleted": True}


def _guide_op(ctx: ResolvedCtx, inp: GuideOpInput) -> dict:
    """Dispatch de la face MCP sur les MÊMES handlers que les faces REST."""
    if inp.op == "list":
        return _list(ctx, _NoInput())
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` requis (cf. op=list).")
    if inp.op == "read":
        # Lecture : `scope` est un FILTRE optionnel (le store cherche dans l'ordre
        # de visibilité user → org → platform quand il est omis).
        return _get(ctx, GuideRefInput(scope=inp.scope or "", slug=inp.slug))
    scope = inp.scope or "user"
    if inp.op == "delete":
        return _delete(ctx, GuideRefInput(scope=scope, slug=inp.slug))
    return _set(ctx, GuideSetInput(scope=scope, slug=inp.slug, body_md=inp.body_md or "",
                                   title=inp.title or "", description=inp.description or ""))


# Autz = SUB_ONLY (authentifié) + garde par SCOPE inline (platform_admin / org_admin / self) :
# le combinateur ne peut pas dériver l'org d'un champ `scope` libre.
# UNE capacité, deux faces (ADR 0042 §Convergence des surfaces) : `oto_guide` op-aware côté
# MCP + les routes `/api/me/guides…` côté dashboard, mêmes handlers. Jusqu'au 2026-07-28 la
# face MCP était un tool écrit à la main (`tools/guide.py`) qui redéclarait sa propre autz.
CAPABILITIES += [
    Capability(
        key="me.guide", handler=_guide_op, Input=GuideOpInput, authz=SUB_ONLY,
        description=(
            "Load or author an oto usage guide (a how-to, PROSE — not a procedure) on demand. "
            "op=list → the catalog you can see (platform ∪ your org ∪ your own) [{slug, scope, "
            "title, description}] ; op=read (slug, optional scope) → its markdown body ; "
            "op=write / delete (slug, scope=platform|org|user, body_md, title?, description?) → "
            "author a guide for the PLATFORM (platform admin), your ORG (org admin) or YOURSELF "
            "(scope=user, the default). Read the relevant guide BEFORE a non-trivial task "
            "(e.g. bulk-load)."),
        mcp="oto_guide",
    ),
    Capability(
        key="me.guides.list", handler=_list, Input=_NoInput, authz=SUB_ONLY, mcp=None,
        description="List the on-demand guides you can see (platform ∪ your org ∪ your own).",
        rest=RestBinding("GET", "/api/me/guides"),
    ),
    Capability(
        key="me.guides.get", handler=_get, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        description="Read one on-demand guide body by scope+slug.",
        rest=RestBinding("GET", "/api/me/guides/{scope}/{slug}"),
    ),
    Capability(
        key="me.guides.set", handler=_set, Input=GuideSetInput, authz=SUB_ONLY, mcp=None,
        description="Create/update an on-demand guide (scope=platform|org|user).",
        rest=RestBinding("PUT", "/api/me/guides/{scope}/{slug}"),
    ),
    Capability(
        key="me.guides.delete", handler=_delete, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        description="Delete an on-demand guide (scope=platform|org|user).",
        rest=RestBinding("DELETE", "/api/me/guides/{scope}/{slug}"),
    ),
]
