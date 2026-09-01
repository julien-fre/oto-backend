"""Override par tenant des documents légaux (`legal_docs.docs_for`), ADR 0052 lot L2.

Sans cette surface, poser les CGU d'un tenant tiers demanderait un `psql` sur la base
partagée — même défaut que le suivi des tenants avant `tenants_admin.py`. Contraste
volontaire avec ce dernier cependant : LÀ, déclarer un tenant reste un runbook
(instance Logto dédiée, hosts…) parce que ça engage de l'infra hors de cette base.
ICI, l'override n'est qu'une ligne de métadonnées (version/label/url) — l'écrire
suffit à la rendre effective, aucun redémarrage, aucun provisioning. D'où une
capacité normale, pas un écran lecture seule.

`tenant_slug` n'est PAS validé contre le registre `tenancy.py` : un override peut se
poser AVANT que l'émetteur du tenant ne soit déclaré (même ordre lâche que
`tenancy.qualify`), et retirer un tenant du registre ne doit pas faire disparaître
en silence l'override qu'on avait posé pour lui. `doc_slug` en revanche EST validé
contre `legal_docs.CURRENT_DOCS` — un override ne peut remplacer que les métadonnées
d'un doc qui existe déjà, jamais en introduire un nouveau (ça demanderait de
retoucher `CONTEXTS`, une décision produit, pas une administration de tenant).
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import db, legal_docs
from ._authz import PLATFORM_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class TenantSlugInput(BaseModel):
    tenant: str


class TenantDocInput(BaseModel):
    tenant: str
    slug: str


class TenantDocSetInput(TenantDocInput):
    version: str
    label: str
    url: str


class TenantLegalDoc(BaseModel):
    slug: str
    version: str
    label: str
    url: str
    overridden: bool                              # False = défaut plateforme, non déclaré par ce tenant


class TenantLegalDocs(BaseModel):
    tenant: str
    docs: list[TenantLegalDoc]


class TenantLegalDocDeleted(BaseModel):
    tenant: str
    slug: str
    deleted: bool


def _known_slug(slug: str) -> str:
    if slug not in legal_docs.CURRENT_DOCS:
        raise AuthzDenied(400, "unknown_doc_slug",
                          f"`{slug}` n'est pas un doc légal connu — "
                          f"attendus : {', '.join(sorted(legal_docs.CURRENT_DOCS))}.")
    return slug


def _list(ctx: ResolvedCtx, inp: TenantSlugInput) -> dict:
    overrides = db.get_tenant_legal_docs(inp.tenant)
    docs = legal_docs.docs_for(inp.tenant)
    return {
        "tenant": inp.tenant,
        "docs": [
            {**meta, "slug": slug, "overridden": slug in overrides}
            for slug, meta in docs.items()
        ],
    }


def _set(ctx: ResolvedCtx, inp: TenantDocSetInput) -> dict:
    slug = _known_slug(inp.slug)
    db.set_tenant_legal_doc(inp.tenant, slug, inp.version, inp.label, inp.url)
    return _list(ctx, TenantSlugInput(tenant=inp.tenant))


def _delete(ctx: ResolvedCtx, inp: TenantDocInput) -> dict:
    slug = _known_slug(inp.slug)
    deleted = db.delete_tenant_legal_doc(inp.tenant, slug)
    return {"tenant": inp.tenant, "slug": slug, "deleted": deleted}


CAPABILITIES += [
    Capability(
        key="admin.legal_docs.list", handler=_list, Input=TenantSlugInput,
        authz=PLATFORM_ADMIN, Output=TenantLegalDocs,
        description="A tenant's effective legal documents (platform default merged "
                    "with any override) and which slugs it has overridden.",
        rest=RestBinding("GET", "/api/admin/tenants/{tenant}/legal-docs"),
    ),
    Capability(
        key="admin.legal_docs.set", handler=_set, Input=TenantDocSetInput,
        authz=PLATFORM_ADMIN, Output=TenantLegalDocs,
        description="Set a tenant's override for one legal doc slug (version, "
                    "label, url) — effective immediately, no restart. `slug` must "
                    "already exist in the platform's CURRENT_DOCS.",
        rest=RestBinding("PUT", "/api/admin/tenants/{tenant}/legal-docs/{slug}"),
    ),
    Capability(
        key="admin.legal_docs.delete", handler=_delete, Input=TenantDocInput,
        authz=PLATFORM_ADMIN, Output=TenantLegalDocDeleted,
        description="Remove a tenant's override for one doc slug — it falls back "
                    "to the platform default immediately.",
        rest=RestBinding("DELETE", "/api/admin/tenants/{tenant}/legal-docs/{slug}"),
    ),
]
