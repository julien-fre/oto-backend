"""L'app OAuth d'un tenant, sur SA surface admin (23/09/2026).

Un tenant qui veut que ses utilisateurs consentent chez un fournisseur sous SA marque
(son projet Google Cloud, ses scopes vérifiés sous son nom) pose son client OAuth
comme **app d'éditeur** du connecteur, keyée par son slug dans l'espace de noms des
tenants (`editor:tenant:<slug>`, cf. `credentials_store` §app d'éditeur et
`google_oauth.app_for`). Jusqu'ici la pose
n'existait que sur la face PLATEFORME (`platform.editor_app.set`, super admin, clé
libre) : un admin de tenant ne pouvait pas poser la sienne depuis son tableau de bord.

Trois capacités, une par geste — lister, poser, retirer — sur
`/api/admin/tenants/{slug}/apps[/{connector}]`. Ce que ce module tient :

- **Le périmètre est le slug de la route, et rien d'autre.** La clé de l'app est CE
  slug : un admin de tenant ne peut ni lire, ni poser, ni retirer l'app d'un autre
  tenant, ni celle de la plateforme (`TENANT_ADMIN_OF("slug")` compare le rôle au sub
  qualifié ; le tenant primaire est refusé à la pose). C'est le sens de « scopé
  tenant » : l'app d'un partenaire ne sert qu'aux comptes qualifiés sous son slug
  (`app_for` lit le slug SUR LE SUB), et ne se pose que depuis chez lui.
- **La pose est REST seule.** Un secret brut ne traverse pas un appel d'outil — même
  règle que les clés de tenant, d'org et de plateforme (25/06).
- **Deux planchers, dans cet ordre** (`TENANT_ADMIN_OF`) : lire = `PLATFORM_ADMIN`,
  poser et retirer = `SUPER_ADMIN` — OU l'admin du tenant lui-même.
- **Ce n'est pas une clé d'accès** : rien ici n'entre dans la cascade de résolution
  (`walk_cascade` ne propose le palier plateforme qu'aux connecteurs qui le déclarent,
  et le rangement `editor:` n'est lu que par le flux de consentement).
- **Liste FERMÉE de connecteurs** (`TENANT_APP_CONNECTORS`) : seuls ceux dont le flux
  LIT l'app d'un tenant. Un connecteur à consentement ne suffit pas : zoho lit une app
  keyée par RÉGION, et une ligne zoho posée par un tenant n'aurait servi à personne —
  mais aurait allumé son bouton de consentement pour toute la plateforme (revue de
  #1063). Tout autre connecteur est refusé en 400 `tenant_app_unsupported`.
- **La réponse nomme le rappel à déclarer chez le fournisseur** : celui du premier host
  que le tenant TIENT (`tenancy.callback_host`), puisque c'est lui que le flux
  enverra ; sans host tenu, le nôtre — et le tenant devra alors autoriser NOTRE domaine
  dans son client.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import credentials_store, tenancy
from ..connectors import flow as connector_flow
from ._authz import PLATFORM_ADMIN, SUPER_ADMIN, TENANT_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES
from .tenant_keys import _known


class TenantAppsInput(BaseModel):
    slug: str


class TenantAppSetInput(BaseModel):
    slug: str
    connector: str
    client_id: str
    client_secret: str


class TenantAppClearInput(BaseModel):
    slug: str
    connector: str


class TenantAppRow(BaseModel):
    """Une app posée — jamais son secret, ni même son `client_id` (inutile pour
    décider, et il finit dans des captures d'écran)."""
    connector: str
    set_at: Optional[str] = None
    callback_url: Optional[str] = None


class TenantApps(BaseModel):
    """Les apps OAuth posées par le tenant. `host` = celui sur lequel leurs rappels
    sont posés (le premier host que le tenant tient), `None` = le nôtre. `eligible` =
    les connecteurs dont le flux LIT l'app d'un tenant, donc qui peuvent en porter une."""
    slug: str
    host: Optional[str] = None
    eligible: list[str]
    apps: list[TenantAppRow]


class TenantAppSet(BaseModel):
    """App posée / rotée. `callback_url` est l'URL EXACTE à déclarer chez le
    fournisseur (« Authorized redirect URIs ») — sans elle, le consentement finit en
    `redirect_uri_mismatch`. ⚠️ `ok: true` dit « écrit et chiffré », pas « ce client
    fonctionne » : rien n'est sondé."""
    ok: bool
    slug: str
    connector: str
    callback_url: Optional[str] = None


class TenantAppCleared(BaseModel):
    """Retrait de l'app. `deleted: false` avec `ok: true` = il n'y avait rien à
    retirer (idempotent, jamais 404). Le tenant retombe sur NOTRE app ; ses comptes
    connectés sous la sienne sont refusés au prochain appel (« reconnecte »), marqués,
    jamais purgés — un jeton ne se rafraîchit qu'avec le client qui l'a émis."""
    ok: bool
    slug: str
    connector: str
    deleted: bool


# Les connecteurs dont le flux de consentement LIT l'app d'un tenant
# (`credentials_store.tenant_app_key`) — aujourd'hui `google_oauth.app_for`, seul.
# Un connecteur n'entre ici qu'avec son lecteur, jamais avant.
TENANT_APP_CONNECTORS: tuple = ("google",)


def _host(slug: str) -> Optional[str]:
    return tenancy.current().callback_host(slug)


def _callback(connector: str, slug: str) -> Optional[str]:
    return connector_flow.callback_url(connector, host=_host(slug))


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _tenant_connector(connector: str) -> str:
    name = (connector or "").strip()
    if name not in TENANT_APP_CONNECTORS:
        raise AuthzDenied(400, "tenant_app_unsupported",
                          f"« {name} » ne lit pas l'app OAuth d'un tenant : posée ici, elle "
                          "ne servirait à personne. Connecteurs qui la lisent : "
                          f"{', '.join(TENANT_APP_CONNECTORS)}.")
    return name


def _list_apps(ctx: ResolvedCtx, inp: TenantAppsInput) -> dict:  # noqa: ARG001
    slug = _known(inp.slug)
    key = credentials_store.tenant_app_key(slug)
    apps = [{"connector": a["connector"], "set_at": _iso(a.get("set_at")),
             "callback_url": _callback(a["connector"], slug)}
            for a in credentials_store.list_editor_apps()
            if a["data_center"] == key and a["connector"] in TENANT_APP_CONNECTORS]
    return {"slug": slug, "host": _host(slug), "eligible": list(TENANT_APP_CONNECTORS),
            "apps": apps}


def _set_app(ctx: ResolvedCtx, inp: TenantAppSetInput) -> dict:
    slug = _known(inp.slug)
    if slug == tenancy.PRIMARY_SLUG:
        raise AuthzDenied(400, "primary_tenant_app",
                          f"Le tenant `{slug}` ne porte pas d'app de tenant : la sienne est "
                          "celle de la plateforme (env), ou /api/admin/editor-apps.")
    name = _tenant_connector(inp.connector)
    try:
        credentials_store.set_editor_app(
            name, credentials_store.tenant_app_key(slug),
            {"client_id": inp.client_id.strip(), "client_secret": inp.client_secret.strip()},
            set_by=ctx.sub)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_app", str(e))
    return {"ok": True, "slug": slug, "connector": name, "callback_url": _callback(name, slug)}


def _clear_app(ctx: ResolvedCtx, inp: TenantAppClearInput) -> dict:  # noqa: ARG001
    slug = _known(inp.slug)
    name = _tenant_connector(inp.connector)
    deleted = credentials_store.clear_editor_app(name, credentials_store.tenant_app_key(slug))
    return {"ok": True, "slug": slug, "connector": name, "deleted": deleted}


CAPABILITIES += [
    Capability(
        key="admin.tenant_apps", handler=_list_apps, Input=TenantAppsInput,
        Output=TenantApps, authz=TENANT_ADMIN_OF("slug", platform=PLATFORM_ADMIN),
        description=("OAuth apps (editor apps) posed by a tenant for the connectors whose "
                     "consent flow reads a tenant app (google), with the callback URL each "
                     "one must declare at the provider — never the secret."),
        rest=RestBinding("GET", "/api/admin/tenants/{slug}/apps"),
    ),
    Capability(
        # REST seule : un secret brut ne traverse pas un appel d'outil (25/06).
        key="admin.tenant_app_set", handler=_set_app, Input=TenantAppSetInput,
        Output=TenantAppSet, authz=TENANT_ADMIN_OF("slug", platform=SUPER_ADMIN),
        description=("Set/rotate the tenant's own OAuth app (client_id + client_secret) "
                     "for a connector whose consent flow reads it (google): its accounts "
                     "then consent under the tenant's brand, and the callback moves to the "
                     "tenant's host. Serves ONLY accounts qualified under this tenant; "
                     "accounts connected under the previous app must reconnect."),
        rest=RestBinding("PUT", "/api/admin/tenants/{slug}/apps/{connector}"),
    ),
    Capability(
        key="admin.tenant_app_clear", handler=_clear_app, Input=TenantAppClearInput,
        Output=TenantAppCleared, authz=TENANT_ADMIN_OF("slug", platform=SUPER_ADMIN),
        description="Remove the tenant's OAuth app for a connector (back to the platform's).",
        rest=RestBinding("DELETE", "/api/admin/tenants/{slug}/apps/{connector}"),
    ),
]
