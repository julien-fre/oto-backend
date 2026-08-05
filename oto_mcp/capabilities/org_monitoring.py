"""Observabilité au niveau ORG — l'étage manquant entre « moi » et « la plateforme ».

Il y avait deux sièges et pas de troisième : un membre voyait SON activité
(`/api/me/activity-summary`, `/api/me/calls`), un opérateur plateforme voyait TOUT
(`oto_admin_monitoring`, ADR 0047) — et le responsable d'une org, rien, hormis
l'export brut du journal d'audit (#67). Or c'est lui qui doit répondre à « qui dans
mon équipe s'en sert », « qu'est-ce qui casse chez nous », « qu'est-ce qui manque à
mes gens ». Ce module ouvre les mêmes lentilles, bornées à SON org.

Scope = **exact et unique** : `tool_calls.org_id` / `usage_signals.org_id`, l'org sous
laquelle l'appel a été émis (seam `current_org`), JAMAIS l'appartenance du membre —
un membre de N orgs n'apporte ici que ce qu'il a fait sous celle-ci. Même règle que
l'export d'audit, donc mêmes chiffres d'un écran à l'autre.

Deux lentilles plateforme ne descendent PAS : `rest` (télémétrie de surface `/api/*` —
santé d'infra, pas usage d'org) et `funnel` (comptes de toute la base). Le funnel a un
pendant org qui répond à la même question à l'échelle d'une équipe : `adoption`.

Surface : console MCP **`oto_org_monitoring(op=…, org_id=…)`** + une route REST par
lentille sous `/api/orgs/{id}/monitoring/*` (per-verbe, idiomatique dashboard — le
verbe en `op` reste la face MCP, cf. monitoring.py). Autz **`ORG_ADMIN_OF`** partout :
le grain nominatif (qui a appelé quoi) est une donnée de responsable, alignée sur
`org.audit_log.export` déjà gaté ainsi.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import db
from . import audit_log, monitoring
from ._authz import ORG_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}


# ── entrées (une par lentille : `org_id` porté par le path REST) ─────────────

class OrgSummaryInput(BaseModel):
    org_id: int
    days: int = 7
    sub: Optional[str] = None      # restreindre à UN membre (email ou sub)


class OrgWindowInput(BaseModel):
    org_id: int
    days: int = 7


class OrgDaysInput(BaseModel):
    org_id: int
    days: int = 30


class OrgCallsInput(BaseModel):
    org_id: int
    limit: int = 200
    sub: Optional[str] = None            # email ou sub
    tool: Optional[str] = None
    errors: bool = False
    days: Optional[int] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    min_duration_ms: Optional[int] = None
    error_contains: Optional[str] = None


class OrgCallInput(BaseModel):
    org_id: int
    call_id: int


class OrgRunsInput(BaseModel):
    org_id: int
    limit: int = 100


class OrgRunInput(BaseModel):
    org_id: int
    run_id: str


# ── handlers ────────────────────────────────────────────────────────────────

def _summary(ctx: ResolvedCtx, inp: OrgSummaryInput) -> dict:
    return db.tool_call_stats(since_days=inp.days, org_id=inp.org_id,
                              sub=monitoring._resolve_sub(inp.sub))


def _calls(ctx: ResolvedCtx, inp: OrgCallsInput) -> dict:
    return {"calls": db.list_tool_calls(
        limit=inp.limit, sub=monitoring._resolve_sub(inp.sub), tool_name=inp.tool,
        errors_only=inp.errors, since_days=inp.days, org_id=inp.org_id,
        run_id=inp.run_id, session_id=inp.session_id,
        min_duration_ms=inp.min_duration_ms, error_contains=inp.error_contains)}


def _call(ctx: ResolvedCtx, inp: OrgCallInput) -> dict:
    """Fiche d'un appel — l'id est un entier séquentiel, donc devinable : la garde
    d'org N'EST PAS une formalité. Un appel d'une autre org rend le MÊME 404 qu'un id
    inexistant (ne pas confirmer son existence)."""
    row = db.get_tool_call(inp.call_id)
    if row is None or row.get("org_id") != inp.org_id:
        raise AuthzDenied(404, "unknown_call",
                          f"Aucun appel id={inp.call_id} dans cette org.")
    return {"call": row}


def _connectors(ctx: ResolvedCtx, inp: OrgWindowInput) -> dict:
    return db.connector_failure_stats(since_days=inp.days, org_id=inp.org_id)


def _adoption(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return db.org_adoption(inp.org_id, active_window_days=inp.days)


def _runs(ctx: ResolvedCtx, inp: OrgRunsInput) -> dict:
    return {"runs": db.list_runs(inp.limit, org_id=inp.org_id)}


def _run(ctx: ResolvedCtx, inp: OrgRunInput) -> dict:
    """Timeline d'un déroulé. Un run_id d'une autre org rend une timeline vide côté
    db → 404 ici (même raisonnement que `_call`, sur une clé opaque cette fois)."""
    calls = db.get_run(inp.run_id, org_id=inp.org_id)
    if not calls:
        raise AuthzDenied(404, "unknown_run",
                          f"Aucun déroulé `{inp.run_id}` dans cette org.")
    return {"run_id": inp.run_id, "calls": calls}


def _gaps(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return {"gaps": db.aggregate_gaps(inp.days, org_id=inp.org_id)}


def _tool_quality(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return {"tools": db.aggregate_tool_feedback(inp.days, org_id=inp.org_id)}


# ── console MCP consolidée `oto_org_monitoring(op=…)` (pattern ADR 0047) ─────

class OrgMonitoringInput(BaseModel):
    op: Literal["summary", "calls", "call", "connectors", "adoption",
                "runs", "run", "gaps", "tool_quality", "export"]
    org_id: int
    days: Optional[int] = None            # fenêtre (défaut 7 ; adoption/gaps/tool_quality : 30)
    limit: Optional[int] = None           # calls (200) / runs (100) / export (1000)
    sub: Optional[str] = None             # summary/calls : filtre membre (email ou sub)
    tool: Optional[str] = None            # calls : filtre outil exact
    errors: bool = False                  # calls : erreurs seulement
    run_id: Optional[str] = None          # run (requis) / calls (filtre)
    session_id: Optional[str] = None      # calls : tous les appels d'une conversation
    min_duration_ms: Optional[int] = None  # calls : appels lents
    error_contains: Optional[str] = None  # calls : recherche dans le message d'erreur
    call_id: Optional[int] = None         # call (requis)
    since: Optional[str] = None           # export : borne basse ISO
    until: Optional[str] = None           # export : borne haute ISO


def _need(val, code: str, msg: str):
    if val is None or (isinstance(val, str) and not val.strip()):
        raise AuthzDenied(400, code, msg)
    return val


def _console(ctx: ResolvedCtx, inp: OrgMonitoringInput) -> dict:
    oid = inp.org_id
    if inp.op == "summary":
        return _summary(ctx, OrgSummaryInput(org_id=oid, days=inp.days or 7, sub=inp.sub))
    if inp.op == "calls":
        return _calls(ctx, OrgCallsInput(
            org_id=oid, limit=inp.limit or 200, sub=inp.sub, tool=inp.tool,
            errors=inp.errors, days=inp.days, run_id=inp.run_id,
            session_id=inp.session_id, min_duration_ms=inp.min_duration_ms,
            error_contains=inp.error_contains))
    if inp.op == "call":
        return _call(ctx, OrgCallInput(org_id=oid, call_id=_need(
            inp.call_id, "missing_call_id", "`call_id` requis pour call.")))
    if inp.op == "connectors":
        return _connectors(ctx, OrgWindowInput(org_id=oid, days=inp.days or 7))
    if inp.op == "adoption":
        return _adoption(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    if inp.op == "runs":
        return _runs(ctx, OrgRunsInput(org_id=oid, limit=inp.limit or 100))
    if inp.op == "run":
        return _run(ctx, OrgRunInput(org_id=oid, run_id=_need(
            inp.run_id, "missing_run_id", "`run_id` requis pour run.")))
    if inp.op == "gaps":
        return _gaps(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    if inp.op == "tool_quality":
        return _tool_quality(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    # export : le journal d'audit org existe déjà (#67) — même autz, même scope,
    # on le REBRANCHE plutôt que d'en écrire un second.
    return audit_log._export(ctx, audit_log.AuditExportInput(
        org_id=oid, since=inp.since, until=inp.until, limit=inp.limit or 1000))


_ADMIN_OF = ORG_ADMIN_OF("org_id")

CAPABILITIES += [
    Capability(key="org.monitoring.summary", handler=_summary, Input=OrgSummaryInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/summary", _ID)),
    Capability(key="org.monitoring.calls", handler=_calls, Input=OrgCallsInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/calls", _ID)),
    Capability(key="org.monitoring.call", handler=_call, Input=OrgCallInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/calls/{call_id}", _ID)),
    Capability(key="org.monitoring.connectors", handler=_connectors, Input=OrgWindowInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/connectors", _ID)),
    Capability(key="org.monitoring.adoption", handler=_adoption, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/adoption", _ID)),
    Capability(key="org.monitoring.runs", handler=_runs, Input=OrgRunsInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/runs", _ID)),
    Capability(key="org.monitoring.run", handler=_run, Input=OrgRunInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/runs/{run_id}", _ID)),
    Capability(key="org.monitoring.gaps", handler=_gaps, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/gaps", _ID)),
    Capability(key="org.monitoring.tool_quality", handler=_tool_quality, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/tool-quality", _ID)),
    Capability(
        key="org.monitoring.console", handler=_console, Input=OrgMonitoringInput,
        authz=_ADMIN_OF,
        description=(
            "Observability of YOUR org (org admin) — `org_id` required. op=summary "
            "(aggregates over the org: totals, by tool w/ avg+p95 latency, by member, by "
            "day; `days`, optional `sub` email|sub) / adoption (member by member: who "
            "actually uses oto, who never did, who is blocked by a connector — the org's "
            "answer to 'is my team on board') / calls (call log of the org, newest first; "
            "filters `sub`, `tool`, `errors`, `days`, `run_id`, `session_id`, "
            "`min_duration_ms`, `error_contains`) / call (`call_id`) / runs · run "
            "(`run_id` → timeline) / connectors (which connector fails to resolve for "
            "your members) / gaps · tool_quality (what YOUR members reported missing or "
            "broken) / export (audit log, `since`/`until` ISO — compliance evidence). "
            "Everything is scoped to calls EMITTED UNDER this org, never to membership. "
            "Platform-wide investigation is oto_admin_monitoring (platform admin)."),
        mcp="oto_org_monitoring",
    ),
]
