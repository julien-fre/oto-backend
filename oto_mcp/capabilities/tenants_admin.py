"""Suivi des tenants (ADR 0052) — console plateforme, lecture seule.

L'étage tenant existait en base et dans le registre d'émetteurs, mais **nulle part
sur un écran** : savoir qui est servi, sous quel émetteur, avec combien d'orgs et de
comptes, demandait un `psql` sur la base partagée. Cette capacité rend ça, aux deux
faces habituelles (dashboard `/platform/tenants` + `oto_admin_tenant` en session).

Trois partis pris, tous conséquences de ce que le tenant EST :

- **Lecture seule — à une exception près, datée.** Déclarer un tenant reste un runbook
  (une instance Logto dédiée, un client OAuth, des hosts sur le proxy — barreau B4) et
  le registre est construit AU BOOT : un formulaire qui poserait un émetteur laisserait
  croire qu'une ligne en base suffit, alors qu'elle ne prend effet qu'au redémarrage et
  ne provisionne rien. **Depuis le 2026-08-29 (L-clés PR 1)**, la seule chose que cette
  surface écrit est la CLÉ DE CONNECTEUR du tenant : `op=keys` la liste, `op=key_clear`
  la retire (SUPER_ADMIN) ; la pose est REST seule (`tenant_keys`, un secret brut ne
  traverse pas un appel d'outil).
- **Les deux sources restent séparées** (`orgs.tenant_id` d'un côté, la qualification
  du sub de l'autre) et l'écart est NOMMÉ (`orgs_desalignees`) — cf. `db/tenants.py`.
- **`PLATFORM_ADMIN`**, comme les autres lentilles de supervision : on lit des
  volumétries et de la configuration d'annuaire, jamais un secret (la table `tenants`
  n'en porte aucun — pas même un credential de management, cf. `ForeignTenantDirectory`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .. import db, tenancy, tool_alias
from . import tenant_admins, tenant_grants, tenant_keys
from ._authz import ADMIN_BY_OP, PLATFORM_ADMIN, SUPER_ADMIN, TENANT_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding, cap_limit
from .registry import CAPABILITIES

# Fenêtre par défaut des compteurs d'activité : 30 j, comme les lentilles d'ADOPTION
# du monitoring (7 j est la fenêtre de trafic ; un tenant se juge sur l'adoption).
_DEFAULT_DAYS = 30
_MAX_DAYS = 365


class TenantsInput(BaseModel):
    days: int = _DEFAULT_DAYS

    @field_validator("days")
    @classmethod
    def _cap_days(cls, v):
        return cap_limit(v, _MAX_DAYS, default=_DEFAULT_DAYS)


class TenantInput(TenantsInput):
    slug: str


# ── forme SERVIE (ADR 0059 : ce qui n'est pas déclaré n'est pas opposable) ────

class TenantRow(BaseModel):
    """Une ligne de suivi : l'identité du tenant, sa configuration d'annuaire, son
    état dans le process courant, et son empreinte sur la fenêtre demandée."""
    id: int
    slug: str
    name: str
    created_at: Optional[str] = None

    # Configuration d'annuaire. Aucun secret : la table `tenants` n'en porte pas
    # (nous n'avons aucun credential de management sur l'émetteur d'un partenaire).
    issuer: Optional[str] = None
    jwks_uri: Optional[str] = None
    hosts: list[str] = Field(default_factory=list)
    oauth_client_id: Optional[str] = None
    dashboard_url: Optional[str] = None
    link_paths: dict = Field(default_factory=dict)
    tool_prefix: Optional[str] = Field(
        default=None, description="Préfixe DÉCLARÉ des outils de la plateforme montrés "
                                  "à ses comptes (`oto_doc` → `<prefix>_doc`). null = "
                                  "les noms canoniques.")

    primary: bool = Field(description="Le tenant de la plateforme (`oto`), dont "
                                      "l'émetteur vient de l'env, pas de la base.")
    issuer_source: Optional[str] = Field(default=None, description="env | db | null")
    authenticates: bool = Field(description="Un émetteur est déclaré — ses jetons "
                                            "peuvent être vérifiés.")
    loaded: bool = Field(description="Présent dans le registre d'émetteurs de CE "
                                     "process (construit au boot).")
    pending_restart: bool = Field(description="Déclaré en base ET absent du registre "
                                              ": ses jetons sont encore rejetés.")
    live_hosts: list[str] = Field(default_factory=list,
                                  description="Hosts effectivement servis par le process.")
    tool_prefix_effectif: Optional[str] = Field(
        default=None, description="Préfixe d'outils réellement APPLIQUÉ par ce process "
                                  "— null alors que `tool_prefix` est posé signifie "
                                  "soit un boot en retard, soit un préfixe refusé "
                                  "(collision de namespace, forme invalide).")

    orgs: int
    orgs_archivees: int
    comptes: int = Field(description="Comptes qualifiés sous ce tenant (préfixe du sub).")
    comptes_actifs: int = Field(description="Comptes ayant appelé un outil sur la fenêtre.")
    appels: int = Field(description="Appels d'outils MCP sur la fenêtre.")
    dernier_compte_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    orgs_desalignees: int = Field(
        description="Orgs rattachées à ce tenant dont le créateur est qualifié sous "
                    "un autre — l'écart entre `orgs.tenant_id` et le sub.")


class TenantTotals(BaseModel):
    tenants: int
    orgs: int
    comptes: int
    comptes_actifs: int
    appels: int


class TenantList(BaseModel):
    tenants: list[TenantRow]
    days: int
    totals: TenantTotals


class TenantSheet(TenantRow):
    """La fiche : la ligne + les listes qui expliquent ses compteurs (bornées à 50)."""
    orgs_recentes: list[dict] = Field(default_factory=list)
    comptes_recents: list[dict] = Field(default_factory=list)
    orgs_desalignees_detail: list[dict] = Field(default_factory=list)


class TenantDetail(BaseModel):
    tenant: TenantSheet
    days: int


class TenantConsoleOut(BaseModel):
    """Enveloppe op-aware : `list` rend `tenants`+`totals`, `get` rend `tenant`,
    `reload` rend `reload`, `keys` rend `keys`, `key_clear` rend `key_clear`.
    Déclarée en union plutôt qu'en intersection (vide) — cf. dette de sortie."""
    days: int
    tenants: Optional[list[TenantRow]] = None
    totals: Optional[TenantTotals] = None
    tenant: Optional[Any] = None
    reload: Optional[Any] = None
    keys: Optional[Any] = None
    key_clear: Optional[Any] = None
    admins: Optional[Any] = None
    admin_add: Optional[Any] = None
    admin_remove: Optional[Any] = None
    org_grants: Optional[Any] = None
    org_grant: Optional[Any] = None
    org_revoke: Optional[Any] = None


def _live_registry() -> dict:
    """Ce que le PROCESS courant sert réellement, par slug.

    La base dit ce qui est déclaré ; le registre dit ce qui est CHARGÉ — il est
    construit au boot, donc une ligne ajoutée depuis n'y est pas. Sans cette
    confrontation, un tenant déclaré mais non redémarré s'affiche « prêt » alors
    qu'aucun de ses jetons ne passe.
    """
    live = {}
    for entry in tenancy.current().entries():
        # Le primaire a deux entrées (émetteur + drain) : la première suffit, elles
        # décrivent le même tenant.
        live.setdefault(entry.slug, {"issuer": entry.issuer, "hosts": list(entry.hosts),
                                     "jwks_uri": entry.jwks_uri,
                                     "oauth_client_id": entry.oauth_client_id,
                                     "tool_prefix": entry.tool_prefix})
    return live


def _decorate(row: dict, live: dict) -> dict:
    """Ajoute l'état RUNTIME à une ligne de suivi (jamais dérivé de la base seule)."""
    entry = live.get(row.get("slug"))
    row["loaded"] = entry is not None
    # Un tenant déclaré en base, porteur d'un émetteur, mais absent du registre du
    # process : le boot est en retard sur la base. C'est le diagnostic qui manquait.
    row["pending_restart"] = bool(row.get("authenticates")) and entry is None
    row["live_hosts"] = list((entry or {}).get("hosts") or [])
    # Le préfixe d'outils passe par la MÊME validation qu'au service (collision de
    # namespace, forme) : l'écran doit montrer ce qui s'applique, pas ce qui est écrit.
    row["tool_prefix_effectif"] = (
        tool_alias.normalize_prefix((entry or {}).get("tool_prefix")) or None)
    return row


def _tenants(ctx: ResolvedCtx, inp: TenantsInput) -> dict:
    live = _live_registry()
    rows = [_decorate(r, live) for r in db.list_tenants_overview(days=inp.days)]
    return {"tenants": rows, "days": inp.days,
            # Le total plateforme n'est pas la somme des tenants : un tenant est une
            # partition des comptes, mais l'écran doit pouvoir le VÉRIFIER.
            "totals": {
                "tenants": len(rows),
                "orgs": sum(r["orgs"] for r in rows),
                "comptes": sum(r["comptes"] for r in rows),
                "comptes_actifs": sum(r["comptes_actifs"] for r in rows),
                "appels": sum(r["appels"] for r in rows),
            }}


def _tenant(ctx: ResolvedCtx, inp: TenantInput) -> dict:
    slug = (inp.slug or "").strip()
    fiche = db.get_tenant_overview(slug, days=inp.days) if slug else None
    if fiche is None:
        raise AuthzDenied(404, "unknown_tenant",
                          f"Aucun tenant `{slug}`. Un tenant se déclare en base "
                          f"(runbook de provisioning), il n'est pas créable ici.")
    return {"tenant": _decorate(fiche, _live_registry()), "days": inp.days}


class ReloadInput(BaseModel):
    """Aucun paramètre : le reload relit la base telle qu'elle est."""


class ReloadOut(BaseModel):
    reloaded: bool
    tenants: list[str] = Field(description="Slugs présents dans le registre APRÈS reload.")
    issuers: int = Field(description="Émetteurs acceptés par le verifier après reload.")
    verifier_updated: bool = Field(
        description="False = process sans serveur construit (tests, scripts) — le "
                    "registre en mémoire a bougé, pas la vérification de jetons.")


def _reload(ctx: ResolvedCtx, inp: ReloadInput) -> dict:
    """La moitié « prise d'effet » du provisionnement (0052 B4) : le runbook déclare
    (instance d'annuaire, client OAuth, hosts, ligne `tenants`) ; CE geste fait lire
    la déclaration par le process qui tourne — fin du verdict `pending_restart` sans
    fenêtre de redémarrage. Import LOCAL de `server` : ce module est chargé par lui.

    ⚠️ Par-process : prod et preprod partagent la base mais pas leur registre —
    recharger l'un ne recharge pas l'autre (même topologie que les `.env`)."""
    from .. import server
    rapport = server.reload_tenant_registry()
    return {"reloaded": True, **rapport}


class TenantConsoleInput(BaseModel):
    op: Literal["list", "get", "reload", "keys", "key_clear",
                "admins", "admin_add", "admin_remove",
                "org_grants", "org_grant", "org_revoke"] = "list"
    slug: Optional[str] = None
    provider: Optional[str] = None      # key_clear, org_grants, org_grant, org_revoke
    account: str = ""                   # key_clear, multi-compte ('' = mono)
    sub: Optional[str] = None           # admin_add, admin_remove
    org_id: Optional[int] = None        # org_grant, org_revoke
    daily_quota: Optional[int] = None   # org_grant (0 = illimité)
    days: int = _DEFAULT_DAYS

    @field_validator("days")
    @classmethod
    def _cap_days(cls, v):
        return cap_limit(v, _MAX_DAYS, default=_DEFAULT_DAYS)


def _console(ctx: ResolvedCtx, inp: TenantConsoleInput) -> dict:
    if inp.op == "reload":
        return {"days": inp.days, "reload": _reload(ctx, ReloadInput())}
    if inp.op == "list":
        return _tenants(ctx, TenantsInput(days=inp.days))
    slug = (inp.slug or "").strip()
    if not slug:
        raise AuthzDenied(400, "missing_slug", f"`slug` requis pour op={inp.op}.")
    if inp.op == "keys":
        return {"days": inp.days,
                "keys": tenant_keys._list_keys(ctx, tenant_keys.TenantKeysInput(slug=slug))}
    if inp.op == "key_clear":
        if not (inp.provider or "").strip():
            raise AuthzDenied(400, "missing_provider", "`provider` requis pour op=key_clear.")
        return {"days": inp.days,
                "key_clear": tenant_keys._clear_key(ctx, tenant_keys.TenantKeyClearInput(
                    slug=slug, provider=inp.provider.strip(), account=inp.account))}
    if inp.op in ("admins", "admin_add", "admin_remove"):
        return {"days": inp.days, **_console_admins(ctx, inp, slug)}
    if inp.op in ("org_grants", "org_grant", "org_revoke"):
        return {"days": inp.days, **_console_grants(ctx, inp, slug)}
    return _tenant(ctx, TenantInput(slug=slug, days=inp.days))


def _console_admins(ctx: ResolvedCtx, inp: TenantConsoleInput, slug: str) -> dict:
    """Le rôle « admin de tenant » (PR 2) : lister, déclarer, retirer."""
    if inp.op == "admins":
        return {"admins": tenant_admins._list(ctx, tenant_admins.TenantAdminsInput(slug=slug))}
    sub = (inp.sub or "").strip()
    if not sub:
        raise AuthzDenied(400, "missing_sub", f"`sub` requis pour op={inp.op}.")
    if inp.op == "admin_add":
        return {"admin_add": tenant_admins._add(
            ctx, tenant_admins.TenantAdminAddInput(slug=slug, sub=sub))}
    return {"admin_remove": tenant_admins._remove(
        ctx, tenant_admins.TenantAdminRemoveInput(slug=slug, sub=sub))}


def _console_grants(ctx: ResolvedCtx, inp: TenantConsoleInput, slug: str) -> dict:
    """L'arête tenant→org de 0053 (PR 2) : lister, accorder, révoquer."""
    provider = (inp.provider or "").strip()
    if not provider:
        raise AuthzDenied(400, "missing_provider", f"`provider` requis pour op={inp.op}.")
    if inp.op == "org_grants":
        return {"org_grants": tenant_grants._list(
            ctx, tenant_grants.TenantOrgGrantsInput(slug=slug, provider=provider))}
    if inp.org_id is None:
        raise AuthzDenied(400, "missing_org_id", f"`org_id` requis pour op={inp.op}.")
    if inp.op == "org_grant":
        return {"org_grant": tenant_grants._grant(ctx, tenant_grants.TenantOrgGrantInput(
            slug=slug, provider=provider, org_id=inp.org_id, daily_quota=inp.daily_quota))}
    return {"org_revoke": tenant_grants._revoke(ctx, tenant_grants.TenantOrgRevokeInput(
        slug=slug, provider=provider, org_id=inp.org_id))}


CAPABILITIES += [
    Capability(key="admin.tenants", handler=_tenants, Input=TenantsInput,
               Output=TenantList, authz=PLATFORM_ADMIN,
               description="Suivi des tenants : une ligne par tenant déclaré.",
               rest=RestBinding("GET", "/api/admin/tenants")),
    Capability(key="admin.tenant", handler=_tenant, Input=TenantInput,
               Output=TenantDetail,
               # PR 2 : l'admin de tenant voit SES orgs (la fiche de son tenant).
               authz=TENANT_ADMIN_OF("slug", platform=PLATFORM_ADMIN),
               description="Fiche d'un tenant : ses compteurs et les listes derrière.",
               rest=RestBinding("GET", "/api/admin/tenants/{slug}")),
    Capability(key="admin.tenants_reload", handler=_reload, Input=ReloadInput,
               Output=ReloadOut, authz=SUPER_ADMIN,
               description="Recharge le registre d'émetteurs depuis la base, sans "
                           "redémarrer — fin du verdict pending_restart pour CE process.",
               rest=RestBinding("POST", "/api/admin/tenants/reload")),
    Capability(
        key="admin.tenant_console", handler=_console, Input=TenantConsoleInput,
        Output=TenantConsoleOut,
        # Lectures = PLATFORM_ADMIN (comme les autres lentilles) ; `reload` touche ce
        # que le process AUTHENTIFIE (les émetteurs acceptés) = SUPER_ADMIN, déclaré
        # au niveau capacité via le combinateur op-aware — jamais dans le handler.
        authz=ADMIN_BY_OP({"list": PLATFORM_ADMIN, "get": PLATFORM_ADMIN,
                           "reload": SUPER_ADMIN,
                           # L-clés PR 1 : lire les clés = lentille ; en retirer une
                           # change ce que la résolution sert à tout un tenant.
                           "keys": PLATFORM_ADMIN, "key_clear": SUPER_ADMIN,
                           # PR 2 — le rôle et l'arête, depuis la console PLATEFORME
                           # seulement : le plancher de l'outil reste `operator` (une
                           # op au rôle de tenant le ferait entrer dans le handshake
                           # de chaque compte) ; l'admin de tenant agit par REST.
                           "admins": PLATFORM_ADMIN, "admin_add": SUPER_ADMIN,
                           "admin_remove": SUPER_ADMIN, "org_grants": PLATFORM_ADMIN,
                           "org_grant": SUPER_ADMIN, "org_revoke": SUPER_ADMIN}),
        description=(
            "[platform admin] Tenant tracking (identity tier, ADR 0052). op=list → one "
            "row per declared tenant: issuer + jwks + hosts + oauth client + dashboard "
            "url, the tool-name prefix shown to its accounts (`tool_prefix` declared "
            "vs `tool_prefix_effectif` actually applied), whether it is LOADED in this "
            "process's issuer registry (declared but not restarted ⇒ its tokens are "
            "still rejected), orgs (via orgs.tenant_id), "
            "accounts (via sub qualification), active accounts + MCP calls over `days` "
            "(default 30), and `orgs_desalignees` — orgs attached to this tenant whose "
            "creator is qualified under another one. op=get (`slug`) adds the lists "
            "behind those counters (orgs, accounts by activity, misaligned orgs). "
            "Declaring a tenant remains a provisioning runbook, not an API — but "
            "op=reload (super_admin) makes THIS process re-read the declarations "
            "without a restart: the issuer registry and the accepted issuers are "
            "swapped live, which clears `pending_restart`. Per-process: prod and "
            "preprod share the DB but not their registry. op=keys (`slug`) → the "
            "connector keys posed on the tenant (never the secret): one shared key "
            "serves every org of the tenant that has no closer key (member/team/org), "
            "resolved before the platform key. op=key_clear (`slug`, `provider`, "
            "optional `account`; super_admin) removes one. Posing a key is REST "
            "only: PUT /api/admin/tenants/{slug}/keys/{provider}. op=admins (`slug`) → "
            "the tenant's admins (accounts qualified under it: they manage the "
            "tenant's keys and org grants from the REST face); admin_add / "
            "admin_remove (`slug`, `sub`; super_admin). op=org_grants (`slug`, "
            "`provider`) → orgs granted the tenant's key with their shared daily "
            "budget and usage; org_grant (`org_id`, optional `daily_quota`, 0 = "
            "unlimited; replaces) / org_revoke (`org_id`; the org falls back to the "
            "platform key) — super_admin. Without any grant the key serves every org "
            "of the tenant; the anonymous endpoint of an org gets it only through a "
            "live grant."),
        mcp="oto_admin_tenant",
    ),
]
