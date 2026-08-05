"""Denylist de tools par org/équipe — gouvernance de visibilité au grain OUTIL,
PAS une barrière de sécurité (ADR 0031, même esprit que
`tool_visibility.DEFAULT_HIDDEN_TOOLS`) : un org_admin/chef d'équipe masque des
tools SPÉCIFIQUES par défaut pour son org/équipe. `user_enabled_tools` (override
perso positif) lève TOUJOURS ce masquage — même échappatoire qu'un masqué-par-
défaut plateforme, un cran plus spécifique. Additif entre paliers (union à la
lecture, `session_visibility.compute_hidden_tools`) : une équipe ne peut JAMAIS
révéler un tool que l'org a masqué (deux ensembles négatifs, jamais un retrait
croisé).

Remplace l'ancienne baseline ALLOWLIST org/équipe (`orgs.default_tools`/
`org_groups.default_tools`, ADR 0012/0015) retirée le 2026-07-03 (commit
`3951a57`) : celle-ci masquait tout ce qui n'était PAS listé — un tool ajouté
après coup arrivait masqué par défaut pour toute org/équipe ayant posé une
baseline. Ici on choisit ce qu'on masque ; le reste, y compris les tools
futurs, reste visible par défaut.

Lecture = `ORG_MEMBER_OF`/`GROUP_MEMBER_OF` (transparence — un membre voit la
gouvernance qui s'applique à lui). Écriture = `ORG_ADMIN_OF`/`GROUP_ADMIN_OF`.
`refresh_visibility=True` : la bascule re-pousse la visibilité sur la session
MCP du caller ; effet pour les autres membres à leur session suivante.
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import db, group_store, org_store, tool_registry
from ._authz import GROUP_ADMIN_OF, GROUP_MEMBER_OF, ORG_ADMIN_OF, ORG_MEMBER_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}
_GID = {"id": "group_id"}


def _known_tool_names() -> set[str]:
    return set(tool_registry.boot_tool_names())


class OrgHiddenToolsListInput(BaseModel):
    org_id: int


class OrgHiddenToolSetInput(BaseModel):
    org_id: int
    name: str                  # tool (placeholder {name}, auto-mappé)


def _org_list(ctx: ResolvedCtx, inp: OrgHiddenToolsListInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    return {"org_id": inp.org_id, "disabled_tools": db.list_org_disabled_tools(inp.org_id)}


def _org_hide(ctx: ResolvedCtx, inp: OrgHiddenToolSetInput) -> dict:
    if inp.name not in _known_tool_names():
        raise AuthzDenied(404, "unknown_tool", f"Tool `{inp.name}` inconnu.")
    db.add_org_disabled_tool(inp.org_id, inp.name, disabled_by=ctx.sub)
    return {"org_id": inp.org_id, "tool": inp.name, "hidden": True}


def _org_unhide(ctx: ResolvedCtx, inp: OrgHiddenToolSetInput) -> dict:
    db.remove_org_disabled_tool(inp.org_id, inp.name)
    return {"org_id": inp.org_id, "tool": inp.name, "hidden": False}


class GroupHiddenToolsListInput(BaseModel):
    group_id: int


class GroupHiddenToolSetInput(BaseModel):
    group_id: int
    name: str                  # tool (placeholder {name}, auto-mappé)


def _group_list(ctx: ResolvedCtx, inp: GroupHiddenToolsListInput) -> dict:
    if not group_store.get_group(inp.group_id):
        raise AuthzDenied(404, "unknown_group", f"Équipe #{inp.group_id} inconnue.")
    return {"group_id": inp.group_id, "disabled_tools": db.list_group_disabled_tools(inp.group_id)}


def _group_hide(ctx: ResolvedCtx, inp: GroupHiddenToolSetInput) -> dict:
    if inp.name not in _known_tool_names():
        raise AuthzDenied(404, "unknown_tool", f"Tool `{inp.name}` inconnu.")
    db.add_group_disabled_tool(inp.group_id, inp.name, disabled_by=ctx.sub)
    return {"group_id": inp.group_id, "tool": inp.name, "hidden": True}


def _group_unhide(ctx: ResolvedCtx, inp: GroupHiddenToolSetInput) -> dict:
    db.remove_group_disabled_tool(inp.group_id, inp.name)
    return {"group_id": inp.group_id, "tool": inp.name, "hidden": False}


CAPABILITIES += [
    Capability(
        key="tools.org_list", handler=_org_list, Input=OrgHiddenToolsListInput,
        authz=ORG_MEMBER_OF("org_id"),
        description="List the tools your org_admin has hidden by default for your org. "
                    "Governance only (ADR 0031) — you can always self-override with "
                    "oto_enable_tool.",
        rest=RestBinding("GET", "/api/orgs/{id}/tools/hidden", _ID),
    ),
    Capability(
        key="tools.org_hide", handler=_org_hide, Input=OrgHiddenToolSetInput,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        description="[org admin] Hide a specific tool by default for your whole org. "
                    "Members can still self-override with oto_enable_tool — this is "
                    "governance, not an access barrier. name = exact tool name.",
        rest=RestBinding("PUT", "/api/orgs/{id}/tools/{name}/hidden", _ID),
    ),
    Capability(
        key="tools.org_unhide", handler=_org_unhide, Input=OrgHiddenToolSetInput,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        description="[org admin] Remove your org's default-hide on a tool.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/tools/{name}/hidden", _ID),
    ),
    Capability(
        key="tools.group_list", handler=_group_list, Input=GroupHiddenToolsListInput,
        authz=GROUP_MEMBER_OF("group_id"),
        description="List the tools your team lead has hidden by default for your team.",
        rest=RestBinding("GET", "/api/groups/{id}/tools/hidden", _GID),
    ),
    Capability(
        key="tools.group_hide", handler=_group_hide, Input=GroupHiddenToolSetInput,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        description="[team lead] Hide a specific tool by default for your team. Additive "
                    "with the org's own denylist (never reveals an org-hidden tool). "
                    "Members can still self-override with oto_enable_tool.",
        rest=RestBinding("PUT", "/api/groups/{id}/tools/{name}/hidden", _GID),
    ),
    Capability(
        key="tools.group_unhide", handler=_group_unhide, Input=GroupHiddenToolSetInput,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        description="[team lead] Remove your team's default-hide on a tool.",
        rest=RestBinding("DELETE", "/api/groups/{id}/tools/{name}/hidden", _GID),
    ),
]
