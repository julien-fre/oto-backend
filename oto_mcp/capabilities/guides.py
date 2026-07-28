"""Le guide — primitif UNIQUE d'instruction (ADR 0042), sur ses deux axes.

Un guide = une unité de prose portée par **`scope`** (platform | org | group | user —
qui l'écrit, à qui elle s'applique) × **`delivery`** (`init` = concaténé dans l'artefact
injecté au handshake · `on-demand` = listé/chargé quand la tâche le demande). Ces deux
axes sont des PARAMÈTRES, pas des concepts distincts : « agent readme », « secret sauce »
et « instructions » ne sont plus que des libellés d'UI (§Convergence des surfaces,
Décision 5).

**Une capacité, deux faces** (ADR 0009) : `oto_guide` op-aware côté MCP + les routes
`/api/me/guides…` côté dashboard, **mêmes handlers, une seule autz par scope**
(`_owner_for_write`). Cœur = `guide_store` (table `guides`, tout-DB depuis le
2026-07-16 ; les fichiers `guides/*.md` ne sont que des seeds de boot).

`op=list` reste le catalogue **on-demand** : un readme `init` est DÉJÀ injecté, l'ajouter
à l'index que l'agent consulte pour charger de la prose serait un doublon de contexte.
On lit/écrit un init explicitement (`delivery='init'`).

Reste distinct : la **PROCÉDURE** (`org_instructions`, slots/versions — prose avec
sémantique d'exécution, ADR 0042 Décision 3) et la **fiche profil** (structurée,
`capabilities/profile.py`, Décision 6).
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


Delivery = Literal["init", "on-demand"]


class GuideRefInput(BaseModel):
    scope: str
    slug: str = ""                       # omis quand delivery='init' (slug canonique)
    delivery: Delivery = "on-demand"


class GuideSetInput(BaseModel):
    scope: str
    slug: str = ""
    delivery: Delivery = "on-demand"
    body_md: str = ""
    title: str = ""
    description: str = ""


class GuideOpInput(BaseModel):
    """Face MCP op-aware (`oto_guide`). `scope` défaut 'user' à l'écriture — un agent
    qui rédige sans préciser écrit POUR SON utilisateur, jamais pour l'org/la plateforme."""
    op: Literal["list", "read", "write", "delete"] = "list"
    slug: Optional[str] = None
    scope: Optional[str] = None
    delivery: Delivery = "on-demand"
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None


def _active_group(ctx: ResolvedCtx) -> int:
    from .. import access
    gid = ctx.group_id if ctx.group_id is not None else access.current_group(ctx.sub)
    if gid is None:
        raise AuthzDenied(400, "no_active_group",
                          "Aucune équipe active — vois `oto_use_group`.")
    return int(gid)


def _owner_for_write(ctx: ResolvedCtx, scope: str) -> str:
    """Le owner_id d'écriture pour `scope`, avec autz. platform → platform_admin ;
    org → org_admin de l'org active ; group → chef de l'équipe active (escalade
    roles.py) ; user → self. Lève AuthzDenied."""
    from .. import roles
    if scope == "user":
        return ctx.sub
    if scope == "org":
        if ctx.org_id is None:
            raise AuthzDenied(400, "no_active_org", "Aucune org active — vois `oto_use_org`.")
        if not roles.is_org_admin(ctx.sub, ctx.org_id):
            raise AuthzDenied(403, "forbidden", "Réservé à un admin de l'org (guide d'org).")
        return str(ctx.org_id)
    if scope == "group":
        gid = _active_group(ctx)
        if not roles.can_admin_group(ctx.sub, gid):
            raise AuthzDenied(403, "forbidden",
                              "Réservé au chef de l'équipe (guide d'équipe).")
        return str(gid)
    if scope == "platform":
        if not roles.is_platform_admin(ctx.sub):
            raise AuthzDenied(403, "forbidden", "Réservé à l'admin plateforme (guide plateforme).")
        return guide_store.PLATFORM_OWNER
    raise AuthzDenied(400, "bad_scope", "scope éditable = platform | org | group | user.")


def _owner_for_read(ctx: ResolvedCtx, scope: str) -> str:
    """Le owner_id de LECTURE d'un readme init : l'identité courante du scope. Pas
    d'autz supplémentaire — on ne lit que SON propre contexte (org/équipe actives),
    exactement ce que le handshake injecte déjà."""
    if scope == "user":
        return ctx.sub
    if scope == "org":
        if ctx.org_id is None:
            raise AuthzDenied(400, "no_active_org", "Aucune org active — vois `oto_use_org`.")
        return str(ctx.org_id)
    if scope == "group":
        return str(_active_group(ctx))
    if scope == "platform":
        return guide_store.PLATFORM_OWNER
    raise AuthzDenied(400, "bad_scope", "scope = platform | org | group | user.")


def _init_ref(ctx: ResolvedCtx, scope: str, slug: str, *, write: bool) -> tuple[str, str]:
    """`(ident passé au store, slug affichable)` d'un readme init.

    ⚠️ Asymétrie de `guide_store` : pour la PLATEFORME l'ident EST le slug du bloc
    (il en existe plusieurs — défaut `secret_sauce`), l'owner étant constant ; pour
    org/group/user l'ident est l'OWNER et le slug est canonique (`readme`) → l'appelant
    n'a pas à le connaître. L'autz passe toujours par le résolveur d'owner du mode."""
    owner = _owner_for_write(ctx, scope) if write else _owner_for_read(ctx, scope)
    if scope == "platform":
        s = slug or guide_store.PLATFORM_SLUG
        return s, s
    return owner, guide_store.INIT_SLUG


def _list(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    """Catalogue des guides on-demand visibles : plateforme ∪ org active ∪ perso (DB)."""
    return {"guides": guide_store.list_guides_for(ctx.sub, ctx.org_id)}


def _init_view(scope: str, slug: str, state: dict) -> dict:
    """Forme d'un readme init, alignée sur celle d'un guide on-demand."""
    return {"scope": scope, "slug": slug, "delivery": "init",
            "body_md": state.get("body_md") or "", "updated_at": state.get("updated_at")}


def _get(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    if inp.delivery == "init":
        # Lecture de SON contexte (org/équipe actives) — ce que le handshake injecte.
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=False)
        return _init_view(inp.scope, slug, guide_store.get_init_guide(inp.scope, ident))
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` requis pour un guide on-demand.")
    # `scope` vide = pas de filtre : le store cherche plateforme → org → user (1er match).
    g = guide_store.read_guide_scoped(inp.slug, scope=inp.scope or None,
                                      org_id=ctx.org_id, sub=ctx.sub)
    if g is None:
        where = f" (scope {inp.scope})" if inp.scope else ""
        raise AuthzDenied(404, "not_found",
                          f"Guide `{inp.slug}`{where} introuvable — liste-les avec op=list.")
    return g


def _set(ctx: ResolvedCtx, inp: GuideSetInput) -> dict:
    body = inp.body_md or ""
    if len(body.encode()) > _MAX_BODY_BYTES:
        raise AuthzDenied(400, "body_too_large", "`body_md` > 64 KB.")
    if inp.delivery == "init":
        # Un corps vide EFFACE la couche (la note se retire comme elle s'écrit) — là où
        # un guide on-demand vide n'aurait aucun sens (rien à charger).
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=True)
        return _init_view(inp.scope, slug, guide_store.set_init_guide(inp.scope, ident, body))
    owner_id = _owner_for_write(ctx, inp.scope)
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` requis pour un guide on-demand.")
    if not body.strip():
        raise AuthzDenied(400, "missing_body", "`body_md` requis.")
    try:
        return guide_store.set_guide(inp.scope, owner_id, inp.slug, body,
                                     inp.title or "", inp.description or "")
    except guide_store.GuideError as e:
        raise AuthzDenied(400, "invalid_guide", str(e))


def _delete(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    if inp.delivery == "init":
        # Retirer une couche injectée = la vider (pas de ligne à supprimer : le rendu
        # omet déjà les couches vides).
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=True)
        guide_store.set_init_guide(inp.scope, ident, "")
        return {"scope": inp.scope, "slug": slug, "delivery": "init", "deleted": True}
    owner_id = _owner_for_write(ctx, inp.scope)
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` requis pour un guide on-demand.")
    deleted = guide_store.delete_guide(inp.scope, owner_id, inp.slug)
    if not deleted:
        raise AuthzDenied(404, "not_found", f"Guide `{inp.slug}` (scope {inp.scope}) absent.")
    return {"scope": inp.scope, "slug": inp.slug, "deleted": True}


def _guide_op(ctx: ResolvedCtx, inp: GuideOpInput) -> dict:
    """Dispatch de la face MCP sur les MÊMES handlers que les faces REST."""
    if inp.op == "list":
        return _list(ctx, _NoInput())
    init = inp.delivery == "init"
    if not inp.slug and not init:
        # Un init a un slug canonique par scope — seul un on-demand doit être nommé.
        raise AuthzDenied(400, "missing_slug", "`slug` requis (cf. op=list).")
    if inp.op == "read":
        # Lecture on-demand : `scope` est un FILTRE optionnel (le store cherche dans
        # l'ordre de visibilité plateforme → org → user quand il est omis). Un init,
        # lui, se lit toujours à un scope donné (défaut : le sien).
        return _get(ctx, GuideRefInput(scope=inp.scope or ("user" if init else ""),
                                       slug=inp.slug or "", delivery=inp.delivery))
    scope = inp.scope or "user"
    if inp.op == "delete":
        return _delete(ctx, GuideRefInput(scope=scope, slug=inp.slug or "",
                                          delivery=inp.delivery))
    return _set(ctx, GuideSetInput(scope=scope, slug=inp.slug or "", delivery=inp.delivery,
                                   body_md=inp.body_md or "",
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
            "Load or author oto INSTRUCTION PROSE — a how-to loaded when needed, or a readme "
            "injected into every session. Two axes: `scope` = platform | org | group | user "
            "(who writes it, whom it applies to) and `delivery` = 'on-demand' (default: a "
            "guide you load for a task) | 'init' (a readme concatenated into what you receive "
            "at handshake). op=list → the on-demand catalog you can see (platform ∪ your org ∪ "
            "your own) ; op=read (slug, optional scope) → its markdown body ; op=write / delete "
            "(slug, scope, body_md, title?, description?) → author for the PLATFORM (platform "
            "admin), your ORG (org admin), your TEAM (team lead) or YOURSELF (scope=user, the "
            "default). With delivery='init' the slug is canonical per scope — omit it (the "
            "user's own readme is `scope='user', delivery='init'`; an empty body clears that "
            "layer). Read the relevant guide BEFORE a non-trivial task (e.g. bulk-load). "
            "This is PROSE: a repeatable process with slots is a procedure (`oto_procedure`), "
            "and what oto knows about the user is their profile card (`oto_profile`)."),
        mcp="oto_guide",
    ),
    Capability(
        key="me.guides.list", handler=_list, Input=_NoInput, authz=SUB_ONLY, mcp=None,
        description="List the on-demand guides you can see (platform ∪ your org ∪ your own).",
        rest=RestBinding("GET", "/api/me/guides"),
    ),
    Capability(
        key="me.guides.get", handler=_get, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        description="Read one guide body by scope+slug (`?delivery=init` for a readme).",
        rest=RestBinding("GET", "/api/me/guides/{scope}/{slug}"),
    ),
    Capability(
        key="me.guides.set", handler=_set, Input=GuideSetInput, authz=SUB_ONLY, mcp=None,
        description=("Create/update a guide (scope=platform|org|group|user). "
                     "`delivery='init'` writes that scope's injected readme (empty body clears it)."),
        rest=RestBinding("PUT", "/api/me/guides/{scope}/{slug}"),
    ),
    Capability(
        key="me.guides.delete", handler=_delete, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        description="Delete a guide (scope=platform|org|group|user).",
        rest=RestBinding("DELETE", "/api/me/guides/{scope}/{slug}"),
    ),
]
