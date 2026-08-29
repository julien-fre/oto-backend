"""Le rôle « admin de tenant » (L-clés PR 2, ADR 0052 — régime transitoire du 27/08).

Le partenaire pose et retire la clé de SON tenant, en accorde l'usage à ses orgs et
voit ses orgs — sans être admin de la plateforme. C'est la sortie nommée du régime
transitoire (l'opérateur du premier tenant tiers est `super_admin` de la plateforme
faute de ce rôle) : quand il existe, le rôle plateforme du partenaire redescend.

Ce que ce module tient :
- **le rôle se lit sur le sub qualifié** (`tenancy.tenant_of`), jamais sur le
  rattachement d'org (lot L1) : un admin déclaré sur `pilote` est un compte
  `pilote:…`, refusé sinon — à la déclaration ici, et à l'appel dans
  `_authz.TENANT_ADMIN_OF` ;
- **le tenant primaire n'a pas d'admin de tenant** : ses admins sont ceux de la
  plateforme, un rôle de plus serait un second mécanisme pour la même fonction ;
- **déclarer le rôle reste un acte de la plateforme** (`SUPER_ADMIN`), comme
  déclarer le tenant — le partenaire, lui, LISTE ses admins.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import db, tenancy
from ._authz import PLATFORM_ADMIN, SUPER_ADMIN, TENANT_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class TenantAdminsInput(BaseModel):
    slug: str


class TenantAdminAddInput(BaseModel):
    slug: str
    sub: str


class TenantAdminRemoveInput(BaseModel):
    slug: str
    sub: str


class TenantAdminRow(BaseModel):
    sub: str
    granted_by: Optional[str] = None
    granted_at: Optional[str] = None


class TenantAdmins(BaseModel):
    """Les comptes admins du tenant — des subs qualifiés sous son slug."""
    slug: str
    admins: list[TenantAdminRow]


class TenantAdminAdded(BaseModel):
    ok: bool
    slug: str
    sub: str


class TenantAdminRemoved(BaseModel):
    """`removed: false` avec `ok: true` = ce compte n'était pas admin (idempotent)."""
    ok: bool
    slug: str
    sub: str
    removed: bool


def _known(slug: str) -> str:
    slug = (slug or "").strip()
    if not slug or not db.tenant_exists(slug):
        raise AuthzDenied(404, "unknown_tenant", f"Aucun tenant `{slug}`.")
    return slug


def _of_tenant(slug: str, sub: str) -> str:
    """Le compte relève-t-il de CE tenant ? Sur le sub qualifié, et rien d'autre."""
    sub = (sub or "").strip()
    if not sub:
        raise AuthzDenied(400, "missing_sub", "`sub` requis.")
    if tenancy.current().tenant_of(sub) != slug:
        raise AuthzDenied(400, "sub_not_of_tenant",
                          f"`{sub}` ne relève pas du tenant `{slug}` (le rôle se lit sur "
                          "le sub qualifié, jamais sur le rattachement d'une org).")
    return sub


def _list(ctx: ResolvedCtx, inp: TenantAdminsInput) -> dict:
    slug = _known(inp.slug)
    return {"slug": slug, "admins": db.list_tenant_admins(slug)}


def _add(ctx: ResolvedCtx, inp: TenantAdminAddInput) -> dict:
    slug = _known(inp.slug)
    if slug == tenancy.PRIMARY_SLUG:
        raise AuthzDenied(400, "primary_tenant",
                          f"Le tenant `{slug}` n'a pas d'admin de tenant : ses admins sont "
                          "ceux de la plateforme (oto_admin_user op=set_role).")
    sub = _of_tenant(slug, inp.sub)
    db.add_tenant_admin(slug, sub, granted_by=ctx.sub)
    return {"ok": True, "slug": slug, "sub": sub}


def _remove(ctx: ResolvedCtx, inp: TenantAdminRemoveInput) -> dict:
    slug = _known(inp.slug)
    sub = (inp.sub or "").strip()
    return {"ok": True, "slug": slug, "sub": sub,
            "removed": bool(db.remove_tenant_admin(slug, sub))}


CAPABILITIES += [
    Capability(
        key="admin.tenant_admins", handler=_list, Input=TenantAdminsInput,
        Output=TenantAdmins, authz=TENANT_ADMIN_OF("slug", platform=PLATFORM_ADMIN),
        description="Tenant admins (accounts qualified under the tenant).",
        rest=RestBinding("GET", "/api/admin/tenants/{slug}/admins"),
    ),
    Capability(
        key="admin.tenant_admin_add", handler=_add, Input=TenantAdminAddInput,
        Output=TenantAdminAdded, authz=SUPER_ADMIN,
        description="Declare a tenant admin (a `sub` qualified under the tenant).",
        rest=RestBinding("POST", "/api/admin/tenants/{slug}/admins"),
    ),
    Capability(
        key="admin.tenant_admin_remove", handler=_remove, Input=TenantAdminRemoveInput,
        Output=TenantAdminRemoved, authz=SUPER_ADMIN,
        description="Withdraw a tenant admin.",
        rest=RestBinding("DELETE", "/api/admin/tenants/{slug}/admins/{sub}"),
    ),
]
