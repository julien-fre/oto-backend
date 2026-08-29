"""L'arête tenant→org de la chaîne 0053, sur la surface admin (L-clés PR 2).

Le tenant ACCORDE l'usage de sa clé de connecteur à l'une de ses orgs, avec un budget
par org (R10 : partagé par tous les membres de l'org — la lettre de D7). Trois états,
les mêmes que la clé plateforme au lot L5 : MUETTE (aucune arête : la clé sert comme
en PR 1), ACCORDE (budget), REFUSE (toutes révoquées : l'org retombe sur la clé
plateforme). Le geste vit chez l'admin de tenant — ou le super admin.

⚠️ L'arête ne re-tenante personne : un compte nu dans une org accordée garde sa
cascade d'avant. Ce qu'elle sert en plus, c'est l'endpoint ANONYME de cette org
(ADR 0032), qui n'a pas d'identité — il n'obtient l'étage tenant que par une arête
vivante, jamais par le rattachement d'org (lot L1).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .. import db, grants_chain, org_store, tenant_vault
from ._authz import PLATFORM_ADMIN, SUPER_ADMIN, TENANT_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class TenantOrgGrantsInput(BaseModel):
    slug: str
    provider: str


class TenantOrgGrantInput(BaseModel):
    slug: str
    provider: str
    org_id: int
    # Appels/jour pour TOUTE l'org (budget partagé, R10). 0 ou absent = illimité.
    daily_quota: Optional[int] = None


class TenantOrgRevokeInput(BaseModel):
    slug: str
    provider: str
    org_id: int


class TenantOrgGrantRow(BaseModel):
    org_id: int
    daily_quota: Optional[int] = None
    used_today: int = Field(description="Appels débités sur l'arête aujourd'hui (org entière).")
    grant_id: int
    created_at: Optional[str] = None


class TenantOrgGrants(BaseModel):
    """Les orgs auxquelles la clé du tenant est ACCORDÉE, avec leur budget. Une org
    absente d'ici n'est pas refusée : sans arête la clé sert comme en PR 1."""
    slug: str
    provider: str
    grants: list[TenantOrgGrantRow]


class TenantOrgGranted(BaseModel):
    ok: bool
    slug: str
    provider: str
    org_id: int
    grant_id: int


class TenantOrgRevoked(BaseModel):
    """`revoked: 0` avec `ok: true` = aucune arête vivante à archiver. ⚠️ Révoquer
    n'est pas « revenir à sans arête » : les arêtes archivées restent, et l'org est
    REFUSÉE sur cette clé (elle retombe sur la plateforme) — c'est ce qui rend la
    révocation vraie."""
    ok: bool
    slug: str
    provider: str
    org_id: int
    revoked: int


def _target(slug: str, provider: str) -> tuple[str, str]:
    slug = (slug or "").strip()
    if not slug or not db.tenant_exists(slug):
        raise AuthzDenied(404, "unknown_tenant", f"Aucun tenant `{slug}`.")
    provider = (provider or "").strip()
    if not tenant_vault.has_tenant_secret(slug, provider):
        raise AuthzDenied(400, "no_tenant_key",
                          f"Le tenant `{slug}` n'a pas de clé `{provider}` : une arête sur "
                          "une clé absente ne servirait rien. Pose la clé d'abord.")
    return slug, provider


def _list(ctx: ResolvedCtx, inp: TenantOrgGrantsInput) -> dict:
    slug, provider = _target(inp.slug, inp.provider)
    return {"slug": slug, "provider": provider,
            "grants": grants_chain.tenant_org_grants(slug, provider)}


def _grant(ctx: ResolvedCtx, inp: TenantOrgGrantInput) -> dict:
    slug, provider = _target(inp.slug, inp.provider)
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    gid = grants_chain.tenant_grant(slug, provider, inp.org_id, inp.daily_quota,
                                    created_by=ctx.sub)
    return {"ok": True, "slug": slug, "provider": provider, "org_id": inp.org_id,
            "grant_id": gid}


def _revoke(ctx: ResolvedCtx, inp: TenantOrgRevokeInput) -> dict:
    slug, provider = _target(inp.slug, inp.provider)
    n = grants_chain.tenant_revoke(slug, provider, inp.org_id)
    return {"ok": True, "slug": slug, "provider": provider, "org_id": inp.org_id,
            "revoked": n}


_PATH = "/api/admin/tenants/{slug}/keys/{provider}/grants"

CAPABILITIES += [
    Capability(
        key="admin.tenant_org_grants", handler=_list, Input=TenantOrgGrantsInput,
        Output=TenantOrgGrants, authz=TENANT_ADMIN_OF("slug", platform=PLATFORM_ADMIN),
        description="Orgs granted the tenant's key, with their daily budget and usage.",
        rest=RestBinding("GET", _PATH),
    ),
    Capability(
        key="admin.tenant_org_grant", handler=_grant, Input=TenantOrgGrantInput,
        Output=TenantOrgGranted, authz=TENANT_ADMIN_OF("slug", platform=SUPER_ADMIN),
        description=("Grant the tenant's key to one org with a shared daily budget "
                     "(`daily_quota`, 0 = unlimited). Replaces a previous grant."),
        rest=RestBinding("PUT", _PATH + "/{org_id}"),
    ),
    Capability(
        key="admin.tenant_org_revoke", handler=_revoke, Input=TenantOrgRevokeInput,
        Output=TenantOrgRevoked, authz=TENANT_ADMIN_OF("slug", platform=SUPER_ADMIN),
        description="Revoke the tenant's key for one org (it falls back to the platform key).",
        rest=RestBinding("DELETE", _PATH + "/{org_id}"),
    ),
]
