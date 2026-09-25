"""L'API d'administration du COMMERCE — sous l'identité de service (oto-backend#1069).

Le commerce est un service à part, consommateur de l'API (ADR 0070 §7.4(b)). Il ne lit
ni n'écrit la base du cœur : il passe par ces capacités, REST seules (`mcp=None`, un
tuyau de service, pas un outil d'agent), sous `/api/service/`, gardées par
`COMMERCE_SERVICE` — aucune n'est ouverte à un compte, fût-il super admin.

Ce qu'il y trouve (conception `oto-commerce`, question 4) : les orgs, leurs membres
PAR ANCIENNETÉ avec leur dernière activité (il désigne les membres payants et écrit ses
relances), l'usage par personne sur une fenêtre, et les droits déclarés de l'org, qu'il
pose et retire. Il ne pose jamais de plan ni de prix : le cœur ne sait pas qui paie.

Le service n'a pas d'org (`ResolvedCtx.org_id` est None) : l'org visée vient TOUJOURS
du chemin, et son existence est vérifiée ici — jamais déduite d'un état de session.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_validator

from .. import db, entitlements_catalogue as catalogue, org_store
from ..db import entitlements as db_entitlements
from ._authz import COMMERCE_SERVICE
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding, cap_limit
from .registry import CAPABILITIES

_ID = {"id": "org_id"}

_ORG_INCONNUE = DeclaredError(404, "unknown_org", "l'org n'existe pas ou est archivée")


def _org_ou_404(org_id: int) -> None:
    if not org_store.get_org(org_id) or org_store.is_archived_org(org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue ou archivée.")


def _iso(v) -> Optional[str]:
    """Une date rendue au service porte TOUJOURS son fuseau (#1073).

    Le store du cœur rend ses dates en TEXTE sans fuseau (« AAAA-MM-JJ HH:MM:SS »,
    `db._conn._str_dict_row`, héritage de SQLite), en UTC puisque la session l'est
    (`TimeZone=UTC`, `_connect_options`). Servies telles quelles, elles sont ambiguës :
    un consommateur ne peut pas les comparer à une date réelle. On les rend en ISO 8601
    avec leur décalage UTC explicite."""
    if v is None:
        return None
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


# ── Les orgs ─────────────────────────────────────────────────────────────────

class ServiceOrgsInput(BaseModel):
    after_id: int = 0
    limit: int = 200

    @field_validator("limit")
    @classmethod
    def _cap(cls, v):
        return cap_limit(v, 1000)


class ServiceOrgRow(BaseModel):
    id: int
    name: str
    created_at: Optional[str] = None
    # Le tenant EFFECTIF de l'org (`db.org_tenant_slug`, union des trois axes) : `oto`
    # pour la nôtre, sinon le slug du tenant tiers qui l'héberge (#1072).
    tenant: str


class ServiceOrgs(BaseModel):
    """Les orgs non archivées d'id > `after_id`, par id croissant. `next_after_id` est
    le curseur de la page suivante, `None` quand la liste est finie.

    ⚠️ Une org dont `tenant` n'est pas `oto` est la cliente d'un partenaire : rien de ce
    qui s'adresse à son titulaire (essai, relance, échéance) ne doit la toucher."""
    orgs: list[ServiceOrgRow]
    next_after_id: Optional[int] = None


def _orgs(ctx: ResolvedCtx, inp: ServiceOrgsInput) -> dict:
    rows = org_store.list_orgs_page(inp.after_id, inp.limit + 1)
    page, suite = rows[:inp.limit], len(rows) > inp.limit
    return {"orgs": [{"id": r["id"], "name": r["name"], "created_at": _iso(r["created_at"]),
                      "tenant": db.org_tenant_slug(r["id"])}
                     for r in page],
            "next_after_id": page[-1]["id"] if suite else None}


# ── Les membres ──────────────────────────────────────────────────────────────

class ServiceOrgInput(BaseModel):
    org_id: int


class ServiceMemberRow(BaseModel):
    sub: str
    email: Optional[str] = None
    org_role: str
    is_active: bool
    joined_at: str
    last_activity_at: Optional[str] = None


class ServiceMembers(BaseModel):
    """Les membres de l'org PAR ANCIENNETÉ (`joined_at`, puis `sub`) — l'ordre dans
    lequel le commerce désigne les membres payants. `last_activity_at` = dernier appel
    d'outil émis sous CETTE org, `None` s'il n'y en a jamais eu."""
    org_id: int
    members: list[ServiceMemberRow]


def _members(ctx: ResolvedCtx, inp: ServiceOrgInput) -> dict:
    _org_ou_404(inp.org_id)
    return {"org_id": inp.org_id,
            "members": [{**r, "joined_at": _iso(r["joined_at"]),
                         "last_activity_at": _iso(r["last_activity_at"])}
                        for r in db.org_members_by_seniority(inp.org_id)]}


# ── L'usage ──────────────────────────────────────────────────────────────────

class ServiceUsageInput(BaseModel):
    org_id: int
    # Fenêtre [since, until), ISO 8601. Omis : du 1er du mois (UTC) à maintenant.
    since: Optional[datetime] = None
    until: Optional[datetime] = None


class ServiceUsageRow(BaseModel):
    sub: str
    calls: int
    platform_calls: int


class ServiceUsage(BaseModel):
    """Par personne, les appels d'outil RÉUSSIS émis sous l'org sur la fenêtre, et
    ceux d'entre eux passés sur une clé de plateforme. Une personne sans appel n'y
    figure pas ; un ancien membre qui a appelé, si."""
    org_id: int
    since: str
    until: str
    by_person: list[ServiceUsageRow]


def _usage(ctx: ResolvedCtx, inp: ServiceUsageInput) -> dict:
    _org_ou_404(inp.org_id)
    until = inp.until or datetime.now(timezone.utc)
    since = inp.since or until.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if since >= until:
        raise AuthzDenied(400, "invalid_window", "`since` doit précéder `until`.")
    return {"org_id": inp.org_id, "since": since.isoformat(), "until": until.isoformat(),
            "by_person": db.org_usage_by_person(inp.org_id, since, until)}


# ── Les droits déclarés ──────────────────────────────────────────────────────

class ServiceEntitlementRow(BaseModel):
    right_key: str
    source: str
    value: int
    sub: Optional[str] = None
    starts_at: Optional[str] = None
    expires_at: Optional[str] = None
    granted_by: Optional[str] = None
    granted_at: Optional[str] = None


class ServiceEntitlements(BaseModel):
    """Toutes les lignes de l'org, portée org (`sub` absent) et personnes, échues et à
    venir comprises : le commerce voit ce qu'il a posé, dates non filtrées."""
    org_id: int
    entitlements: list[ServiceEntitlementRow]


def _ligne(r: dict) -> dict:
    return {k: (_iso(r[k]) if k in ("starts_at", "expires_at", "granted_at") else r[k])
            for k in ServiceEntitlementRow.model_fields}


def _entitlements(ctx: ResolvedCtx, inp: ServiceOrgInput) -> dict:
    _org_ou_404(inp.org_id)
    return {"org_id": inp.org_id,
            "entitlements": [_ligne(r) for r in db_entitlements.list_for_org(inp.org_id)]}


class ServiceEntitlementKey(BaseModel):
    org_id: int
    right_key: str
    source: str
    # La personne visée dans l'org ; omise = le droit vaut pour toute l'org.
    sub: Optional[str] = None


class ServiceEntitlementPutInput(ServiceEntitlementKey):
    value: int
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


_ERREURS_CLE = (
    _ORG_INCONNUE,
    DeclaredError(400, "unknown_source",
                  f"`source` hors de la liste fermée ({', '.join(catalogue.SOURCES)})"),
    DeclaredError(404, "not_a_member", "la personne `sub` n'est pas membre de l'org"),
)


def _cle_valide(inp: ServiceEntitlementKey) -> None:
    _org_ou_404(inp.org_id)
    if inp.source not in catalogue.SOURCES:
        raise AuthzDenied(400, "unknown_source",
                          f"Source {inp.source!r} inconnue (attendu l'une de "
                          f"{', '.join(catalogue.SOURCES)}).")
    if inp.sub is not None and org_store.get_org_role(inp.org_id, inp.sub) is None:
        raise AuthzDenied(404, "not_a_member",
                          f"{inp.sub!r} n'est pas membre de l'org #{inp.org_id}.")


def _put(ctx: ResolvedCtx, inp: ServiceEntitlementPutInput) -> dict:
    _cle_valide(inp)
    if inp.expires_at and inp.starts_at and inp.expires_at <= inp.starts_at:
        raise AuthzDenied(400, "invalid_window", "`expires_at` doit suivre `starts_at`.")
    # Le catalogue juge AVANT la pose (qui le rejugerait) : chaque refus a son code ici.
    try:
        catalogue.droit(inp.right_key)
    except ValueError as e:
        raise AuthzDenied(400, "entitlement_unknown_key", str(e))
    try:
        catalogue.valeur_valide(inp.right_key, inp.value)
    except ValueError as e:
        raise AuthzDenied(400, "entitlement_value_invalid", str(e))
    db_entitlements.grant(inp.org_id, inp.right_key, inp.source, value=inp.value,
                          sub=inp.sub, starts_at=inp.starts_at, expires_at=inp.expires_at,
                          granted_by=ctx.sub)
    ligne = next(r for r in db_entitlements.list_for_org(inp.org_id)
                 if (r["right_key"], r["source"], r["sub"]) == (inp.right_key, inp.source, inp.sub))
    return _ligne(ligne)


def _delete(ctx: ResolvedCtx, inp: ServiceEntitlementKey) -> dict:
    _cle_valide(inp)
    if not db_entitlements.revoke(inp.org_id, inp.right_key, inp.source, sub=inp.sub):
        raise AuthzDenied(404, "unknown_entitlement",
                          f"Aucun droit {inp.right_key!r} de source {inp.source!r} "
                          f"posé sur {'cette personne' if inp.sub else 'cette org'}.")
    return {"ok": True}


class ServiceOk(BaseModel):
    ok: bool


_CHEMIN_DROIT = "/api/service/orgs/{id}/entitlements/{right_key}/{source}"

CAPABILITIES += [
    Capability(key="service.orgs.list", handler=_orgs, Input=ServiceOrgsInput,
               authz=COMMERCE_SERVICE, mcp=None, Output=ServiceOrgs,
               description="[service commerce] Non-archived orgs by increasing id, "
                           "paginated by `after_id`, each with its effective `tenant` "
                           "(`oto` = ours; anything else belongs to a partner).",
               rest=RestBinding("GET", "/api/service/orgs")),
    Capability(key="service.org.members", handler=_members, Input=ServiceOrgInput,
               authz=COMMERCE_SERVICE, mcp=None, Output=ServiceMembers,
               errors=(_ORG_INCONNUE,),
               description="[service commerce] Members of an org by seniority "
                           "(`joined_at`), with email and last activity under this org.",
               rest=RestBinding("GET", "/api/service/orgs/{id}/members", _ID)),
    Capability(key="service.org.usage", handler=_usage, Input=ServiceUsageInput,
               authz=COMMERCE_SERVICE, mcp=None, Output=ServiceUsage,
               errors=(_ORG_INCONNUE,
                       DeclaredError(400, "invalid_window", "`since` n'est pas avant `until`")),
               description="[service commerce] Successful tool calls per person under an "
                           "org over [since, until) (default: current month, UTC), and "
                           "how many ran on a platform key.",
               rest=RestBinding("GET", "/api/service/orgs/{id}/usage", _ID)),
    Capability(key="service.org.entitlements.list", handler=_entitlements,
               Input=ServiceOrgInput, authz=COMMERCE_SERVICE, mcp=None,
               Output=ServiceEntitlements, errors=(_ORG_INCONNUE,),
               description="[service commerce] Declared entitlements of an org, org-wide "
                           "and per person, expired and future included.",
               rest=RestBinding("GET", "/api/service/orgs/{id}/entitlements", _ID)),
    Capability(key="service.org.entitlement.put", handler=_put,
               Input=ServiceEntitlementPutInput, authz=COMMERCE_SERVICE, mcp=None,
               Output=ServiceEntitlementRow,
               errors=_ERREURS_CLE + (
                   DeclaredError(400, "entitlement_unknown_key", "clé hors catalogue"),
                   DeclaredError(400, "entitlement_value_invalid",
                                 "valeur hors du genre de la clé"),
                   DeclaredError(400, "invalid_window", "`expires_at` ne suit pas `starts_at`"),
               ),
               description="[service commerce] Set (idempotent upsert) one entitlement: "
                           "one row per (org, person, right, source). `sub` omitted = the "
                           "whole org. Returns the stored row.",
               rest=RestBinding("PUT", _CHEMIN_DROIT, _ID)),
    Capability(key="service.org.entitlement.delete", handler=_delete,
               Input=ServiceEntitlementKey, authz=COMMERCE_SERVICE, mcp=None,
               Output=ServiceOk,
               errors=_ERREURS_CLE + (
                   DeclaredError(404, "unknown_entitlement", "aucune ligne à retirer"),
               ),
               description="[service commerce] Remove one entitlement row (org, person, "
                           "right, source); other sources and scopes stay.",
               rest=RestBinding("DELETE", _CHEMIN_DROIT, _ID)),
]
