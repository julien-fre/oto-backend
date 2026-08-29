"""La clé de connecteur d'un tenant, sur la surface admin (L-clés PR 1, ADR 0052).

Trois capacités, une par geste — lister, poser, retirer — sur `/api/admin/tenants/
{slug}/keys[/{provider}]`, plus deux ops de la console `oto_admin_tenant` (`keys`,
`key_clear`) déclarées dans `tenants_admin`. Ce que ce module tient :

- **La pose est REST seule.** Un secret brut ne traverse pas un appel d'outil — la
  règle vaut pour les clés d'org et les clés plateforme depuis le 2026-06-25
  (`admin_console`, `orgs/secrets`), elle vaut ici. La console liste et retire.
- **Le plancher est celui de l'opérateur de la plateforme.** Lire = `PLATFORM_ADMIN`
  (une lentille) ; poser et retirer = `SUPER_ADMIN`, comme `reload` : ça change ce que
  la résolution sert à tout un tenant. Le rôle « admin de tenant » (le partenaire pose
  SA clé depuis SON tableau de bord) et l'arête tenant→org de la chaîne 0053 sont la
  PR 2 — cette surface-ci est la première marche, pas le produit.
- **Le tenant primaire est refusé** : ses clés partagées sont les instances plateforme.
- **Même validation qu'une clé d'org** (`providers.org_secret_meta`, écriture partielle
  par merge #448, garde de compte #409) — source unique, rien de recopié.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .. import credentials_store, db, providers, tenancy, tenant_vault
from ._authz import PLATFORM_ADMIN, SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class TenantKeysInput(BaseModel):
    slug: str


class TenantKeySetInput(BaseModel):
    slug: str
    provider: str
    api_key: str = ""                          # connecteurs mono-champ (clé simple)
    fields: Optional[dict[str, str]] = None    # connecteurs multi-champs (zoho/silae…)
    base_url: Optional[str] = None             # connecteurs remote (endpoint du bridge)
    account: str = ""                          # multi-compte ('' = mono)


class TenantKeyClearInput(BaseModel):
    slug: str
    provider: str
    account: str = ""


class TenantKeyRow(BaseModel):
    provider: str
    account: str = ""
    set_by: Optional[str] = None
    set_at: Optional[str] = None
    base_url: Optional[str] = None


class TenantKeys(BaseModel):
    """Les clés de connecteur posées sur le tenant — jamais leur valeur."""
    slug: str
    keys: list[TenantKeyRow]


class TenantKeySet(BaseModel):
    """Clé de connecteur du tenant posée / rotée. La réponse n'écho rien de ce qui a
    été écrit. ⚠️ `ok: true` dit « écrit et chiffré », pas « ce credential fonctionne »."""
    ok: bool
    slug: str
    provider: str


class TenantKeyCleared(BaseModel):
    """Retrait de la clé. `deleted: false` avec `ok: true` = il n'y avait rien à
    retirer (idempotent, jamais 404). Les orgs du tenant retombent sur la clé
    plateforme si elle leur est accordée — la cascade, pas un repli."""
    ok: bool
    slug: str
    provider: str
    account: str
    deleted: bool


def _known(slug: str) -> str:
    slug = (slug or "").strip()
    if not slug or not db.tenant_exists(slug):
        raise AuthzDenied(404, "unknown_tenant",
                          f"Aucun tenant `{slug}`. Un tenant se déclare en base "
                          "(runbook de provisioning), il n'est pas créable ici.")
    return slug


def _list_keys(ctx: ResolvedCtx, inp: TenantKeysInput) -> dict:
    slug = _known(inp.slug)
    return {"slug": slug, "keys": tenant_vault.list_tenant_secrets(slug)}


def _set_key(ctx: ResolvedCtx, inp: TenantKeySetInput) -> dict:
    slug = _known(inp.slug)
    if slug == tenancy.PRIMARY_SLUG:
        raise AuthzDenied(400, "primary_tenant_key",
                          f"Le tenant `{slug}` ne porte pas de clé de tenant : ses clés "
                          "partagées sont les instances plateforme (/api/admin/platform-keys).")
    base_url = (inp.base_url or "").strip() or None
    meta, code = providers.org_secret_meta(inp.provider, base_url)
    if code:
        raise AuthzDenied(400, code, f"Provider/base_url invalide : {code}.")
    account = (inp.account or "").strip()
    fields = inp.fields
    if fields is not None:
        fields = credentials_store.merge_with_existing(
            credentials_store.TENANT, slug, inp.provider, account, fields)
    try:
        secret = credentials_store.secret_from_input(inp.provider, inp.api_key, fields)
    except ValueError as e:
        raise AuthzDenied(400, getattr(e, "code", str(e)),
                          getattr(e, "message", "Credential incomplet ou vide."))
    # Pas d'org de contexte pour une clé de tenant : la cardinalité se lit au défaut
    # de la plateforme (`connectors.cardinality`), pas à une surcharge d'org.
    try:
        credentials_store.guard_account_write(credentials_store.TENANT, slug, inp.provider,
                                              account)
    except credentials_store.NamedAccountRequired as e:
        raise AuthzDenied(409, "account_required", str(e))
    except credentials_store.SingleAccountConnector as e:
        raise AuthzDenied(400, "single_account_connector", str(e))
    try:
        tenant_vault.set_tenant_secret(slug, inp.provider, secret, set_by=ctx.sub,
                                       meta=meta, account=account)
    except tenant_vault.PrimaryTenantKeyRefused as e:
        raise AuthzDenied(400, "primary_tenant_key", str(e))
    return {"ok": True, "slug": slug, "provider": inp.provider}


def _clear_key(ctx: ResolvedCtx, inp: TenantKeyClearInput) -> dict:
    slug = _known(inp.slug)
    account = (inp.account or "").strip()
    deleted = tenant_vault.delete_tenant_secret(slug, inp.provider, account=account)
    return {"ok": True, "slug": slug, "provider": inp.provider, "account": account,
            "deleted": deleted}


CAPABILITIES += [
    Capability(
        key="admin.tenant_keys", handler=_list_keys, Input=TenantKeysInput,
        Output=TenantKeys, authz=PLATFORM_ADMIN,
        description="Connector keys posed on a tenant (never the secret).",
        rest=RestBinding("GET", "/api/admin/tenants/{slug}/keys"),
    ),
    Capability(
        # REST seule : un secret brut ne traverse pas un appel d'outil (25/06).
        key="admin.tenant_key_set", handler=_set_key, Input=TenantKeySetInput,
        Output=TenantKeySet, authz=SUPER_ADMIN,
        description=("Set/rotate a tenant's shared connector key (org-shareable "
                     "connectors only; serves every org of the tenant that has no "
                     "closer key). Single-key connectors: `api_key`; multi-field: "
                     "`fields`; remote bridges: `base_url`."),
        rest=RestBinding("PUT", "/api/admin/tenants/{slug}/keys/{provider}"),
    ),
    Capability(
        key="admin.tenant_key_clear", handler=_clear_key, Input=TenantKeyClearInput,
        Output=TenantKeyCleared, authz=SUPER_ADMIN,
        description="Remove a tenant's shared connector key.",
        rest=RestBinding("DELETE", "/api/admin/tenants/{slug}/keys/{provider}"),
    ),
]
