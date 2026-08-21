"""Suivi des tenants (ADR 0052) — console plateforme, lecture seule.

L'étage tenant existait en base et dans le registre d'émetteurs, mais **nulle part
sur un écran** : savoir qui est servi, sous quel émetteur, avec combien d'orgs et de
comptes, demandait un `psql` sur la base partagée. Cette capacité rend ça, aux deux
faces habituelles (dashboard `/platform/tenants` + `oto_admin_tenant` en session).

Trois partis pris, tous conséquences de ce que le tenant EST :

- **Lecture seule.** Déclarer un tenant reste un runbook (une instance Logto dédiée,
  un client OAuth, des hosts sur le proxy — barreau B4) et le registre est construit
  AU BOOT : un formulaire qui poserait un émetteur laisserait croire qu'une ligne en
  base suffit, alors qu'elle ne prend effet qu'au redémarrage et ne provisionne rien.
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
from ._authz import PLATFORM_ADMIN
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
    """Enveloppe op-aware : `list` rend `tenants`+`totals`, `get` rend `tenant`.
    Déclarée en union plutôt qu'en intersection (vide) — cf. dette de sortie."""
    days: int
    tenants: Optional[list[TenantRow]] = None
    totals: Optional[TenantTotals] = None
    tenant: Optional[Any] = None


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


class TenantConsoleInput(BaseModel):
    op: Literal["list", "get"] = "list"
    slug: Optional[str] = None
    days: int = _DEFAULT_DAYS

    @field_validator("days")
    @classmethod
    def _cap_days(cls, v):
        return cap_limit(v, _MAX_DAYS, default=_DEFAULT_DAYS)


def _console(ctx: ResolvedCtx, inp: TenantConsoleInput) -> dict:
    if inp.op == "get":
        if not (inp.slug or "").strip():
            raise AuthzDenied(400, "missing_slug", "`slug` requis pour op=get.")
        return _tenant(ctx, TenantInput(slug=inp.slug, days=inp.days))
    return _tenants(ctx, TenantsInput(days=inp.days))


CAPABILITIES += [
    Capability(key="admin.tenants", handler=_tenants, Input=TenantsInput,
               Output=TenantList, authz=PLATFORM_ADMIN,
               description="Suivi des tenants : une ligne par tenant déclaré.",
               rest=RestBinding("GET", "/api/admin/tenants")),
    Capability(key="admin.tenant", handler=_tenant, Input=TenantInput,
               Output=TenantDetail, authz=PLATFORM_ADMIN,
               description="Fiche d'un tenant : ses compteurs et les listes derrière.",
               rest=RestBinding("GET", "/api/admin/tenants/{slug}")),
    Capability(
        key="admin.tenant_console", handler=_console, Input=TenantConsoleInput,
        Output=TenantConsoleOut, authz=PLATFORM_ADMIN,
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
            "Read-only: declaring a tenant is a provisioning runbook, not an API."),
        mcp="oto_admin_tenant",
    ),
]
