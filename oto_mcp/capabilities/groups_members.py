"""Capacités d'écriture sur les membres d'un groupe (ADR 0012).

Autz = `GROUP_ADMIN_OF` (chef d'équipe, org_admin parent, ou platform_admin par
escalade `roles`). INVARIANT : on n'ajoute au groupe qu'un membre DÉJÀ dans l'org
parente (l'appartenance groupe est subordonnée à l'org). Garde « dernier chef »
au niveau handler.
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import db, group_store, org_store
from ._authz import GROUP_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_GID = {"id": "group_id"}


def _resolve_target(target: str) -> str:
    target = (target or "").strip()
    if not target:
        raise AuthzDenied(400, "missing_target", "Cible (email ou sub) requise.")
    if "@" in target:
        user = db.get_user_by_email(target)
        if not user:
            raise AuthzDenied(400, "unknown_user",
                              f"Aucun user connu avec l'email `{target}`.")
        return user["sub"]
    return target


def _check_role(role: str) -> str:
    if role not in group_store.GROUP_ROLES:
        raise AuthzDenied(400, "invalid_role", f"Rôle de groupe invalide : {role!r}.")
    return role


class AddGroupMemberInput(BaseModel):
    group_id: int
    target: str
    role: str = "group_member"


class SetGroupMemberRoleInput(BaseModel):
    group_id: int
    sub: str
    role: str


class RemoveGroupMemberInput(BaseModel):
    group_id: int
    target: str


# --- Sorties ----------------------------------------------------------------

class GroupMemberWritten(BaseModel):
    """Écho d'un ajout de membre d'équipe ou d'un changement de rôle.

    ⚠️ `sub` est le sub **RÉSOLU** : l'entrée accepte un email, la réponse ne le
    renvoie jamais — garder sa propre trace pour rapprocher requête et réponse.

    ⚠️ **L'ajout est un UPSERT** (`ON CONFLICT DO UPDATE`) : ajouter quelqu'un déjà
    membre de l'équipe ne rend pas de conflit, ça écrase son rôle. `POST /members` sur
    un membre existant est donc un changement de rôle déguisé — et il **ne passe PAS**
    par l'anti-lockout de `POST /members/{sub}` (409 `last_group_admin`) : rétrograder
    le dernier chef d'équipe est refusé sur une route, accepté sur l'autre. Même défaut
    qu'#273 côté org, un palier plus bas ; à fermer de la même façon.

    ⚠️ **Le décompte des chefs ignore l'escalade** : `last_group_admin` compte les
    lignes `group_admin` EXPLICITES. Deux conséquences opposées, également surprenantes
    — on refuse de rétrograder le « dernier » chef d'une équipe qu'un org_admin peut de
    toute façon administrer ; et une équipe peut vivre avec **zéro** chef explicite (cf.
    `GroupCreated` : créée par quelqu'un d'extérieur à l'org), auquel cas la garde ne
    protège rien.

    ⚠️ Aucun effet sur l'équipe ACTIVE : ajouter quelqu'un ne le fait pas travailler
    sous cette équipe (il faut qu'il la choisisse). Tant qu'il ne l'a pas fait, le
    secret partagé de l'équipe ne le sert pas."""
    ok: bool
    group_id: int
    sub: str
    # Rôle EFFECTIF après écriture, validé contre `group_store.GROUP_ROLES`.
    role: str


class GroupMemberRemoved(BaseModel):
    """Retrait d'un membre d'équipe. ⚠️ `removed` ne vaut **jamais** `false` : l'absence
    d'appartenance lève un 404 et le dernier chef d'équipe un 409. C'est une constante
    d'écho, pas un verdict à tester.

    ⚠️ Effet de bord invisible dans la réponse : si l'équipe retirée était l'équipe
    ACTIVE de la personne, elle se retrouve **sans équipe active** — donc au niveau org
    à son appel suivant, et **plus servie par le secret partagé de l'équipe** (bascule
    silencieuse vers le secret d'org ou le grant plateforme, sans erreur).

    `sub` est le sub RÉSOLU (l'entrée acceptait un email)."""
    ok: bool
    group_id: int
    sub: str
    removed: bool


def _add_member(ctx: ResolvedCtx, inp: AddGroupMemberInput) -> dict:
    role = _check_role(inp.role)
    target_sub = _resolve_target(inp.target)
    # ctx.org_id = org parente injectée par GROUP_ADMIN_OF (jamais un param client).
    if org_store.get_org_role(ctx.org_id, target_sub) is None:
        raise AuthzDenied(409, "not_org_member",
                          "La cible doit d'abord être membre de l'org parente.")
    group_store.add_group_member(inp.group_id, target_sub, role)
    return {"ok": True, "group_id": inp.group_id, "sub": target_sub, "role": role}


def _set_member_role(ctx: ResolvedCtx, inp: SetGroupMemberRoleInput) -> dict:
    role = _check_role(inp.role)
    current = group_store.get_group_role(inp.group_id, inp.sub)
    if current is None:
        raise AuthzDenied(404, "not_a_member", "Cible non-membre du groupe.")
    if current == "group_admin" and role != "group_admin" \
            and group_store.count_group_admins(inp.group_id) <= 1:
        raise AuthzDenied(409, "last_group_admin",
                          "Impossible de rétrograder le dernier chef d'équipe.")
    group_store.add_group_member(inp.group_id, inp.sub, role)
    return {"ok": True, "group_id": inp.group_id, "sub": inp.sub, "role": role}


def _remove_member(ctx: ResolvedCtx, inp: RemoveGroupMemberInput) -> dict:
    target_sub = _resolve_target(inp.target)
    if group_store.get_group_role(inp.group_id, target_sub) == "group_admin" \
            and group_store.count_group_admins(inp.group_id) <= 1:
        raise AuthzDenied(409, "last_group_admin",
                          "Impossible de retirer le dernier chef d'équipe.")
    if not group_store.remove_group_member(inp.group_id, target_sub):
        raise AuthzDenied(404, "not_a_member", "Cible non-membre du groupe.")
    return {"ok": True, "group_id": inp.group_id, "sub": target_sub, "removed": True}


CAPABILITIES += [
    Capability(
        key="group.member.add", handler=_add_member, Input=AddGroupMemberInput,
        authz=GROUP_ADMIN_OF("group_id"), Output=GroupMemberWritten,
        description=("Add a member (by email or sub) to a group you lead. The target "
                     "must already belong to the parent org. role: group_member|group_admin."),
        rest=RestBinding("POST", "/api/groups/{id}/members", _GID),
    ),
    Capability(
        key="group.member.set_role", handler=_set_member_role, Input=SetGroupMemberRoleInput,
        authz=GROUP_ADMIN_OF("group_id"), Output=GroupMemberWritten,
        description="Change a member's role in a group you lead (group_member|group_admin).",
        rest=RestBinding("POST", "/api/groups/{id}/members/{sub}", _GID),
    ),
    Capability(
        key="group.member.remove", handler=_remove_member, Input=RemoveGroupMemberInput,
        authz=GROUP_ADMIN_OF("group_id"), Output=GroupMemberRemoved,
        description="Remove a member (by email or sub) from a group you lead.",
        rest=RestBinding("DELETE", "/api/groups/{id}/members/{sub}",
                         {"id": "group_id", "sub": "target"}),
    ),
]
