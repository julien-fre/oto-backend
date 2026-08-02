"""Capacités monitoring / investigation plateforme (ADR 0009/0017, console ADR 0047).

Les lentilles d'observabilité `/api/admin/monitoring/*` migrent des routes écrites
main d'`api_routes.py` vers des capacités co-déclarées — mêmes chemins, même autz
(PLATFORM_ADMIN), mêmes payloads (contrat dashboard inchangé) — et gagnent leur
face MCP via la console consolidée `oto_admin_monitoring(op=…)` : l'agent
plateforme investigue EN SESSION (drill-down agrégats → journal filtré → fiche
d'appel → run/session), plus seulement via le dashboard.

Les projections runs/gaps/tool_quality restent déclarées dans `usage.py` (leur
domicile ADR 0017) ; la console les réutilise telles quelles. Les signaux ont
déjà leur console (`oto_admin_signal`) — pas dupliqués ici.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import db
from . import usage
from ._authz import PLATFORM_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


def _resolve_sub(target: Optional[str]) -> Optional[str]:
    """Filtre appelant en email OU sub → sub (confort agentique : on investigue
    « les appels de jb@… », pas d'un sub opaque). None passe tel quel."""
    if not target:
        return None
    from .orgs_members import _resolve_target
    return _resolve_target(target)


# ── lentilles (une capacité par verbe, faces REST idem routes historiques) ───

class SummaryInput(BaseModel):
    days: int = 7
    org_id: Optional[int] = None   # restreindre à UN workspace
    sub: Optional[str] = None      # restreindre à UN appelant (email ou sub)


class WindowInput(BaseModel):
    days: int = 7


class FunnelInput(BaseModel):
    days: int = 30


class CallsInput(BaseModel):
    limit: int = 200
    sub: Optional[str] = None            # email ou sub
    tool: Optional[str] = None
    errors: bool = False
    days: Optional[int] = None
    org_id: Optional[int] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    min_duration_ms: Optional[int] = None
    error_contains: Optional[str] = None


class CallInput(BaseModel):
    call_id: int


def _summary(ctx: ResolvedCtx, inp: SummaryInput) -> dict:
    return db.tool_call_stats(since_days=inp.days, org_id=inp.org_id,
                              sub=_resolve_sub(inp.sub))


def _rest_stats(ctx: ResolvedCtx, inp: WindowInput) -> dict:
    return db.rest_call_stats(since_days=inp.days)


def _connector_stats(ctx: ResolvedCtx, inp: WindowInput) -> dict:
    return db.connector_failure_stats(since_days=inp.days)


def _funnel(ctx: ResolvedCtx, inp: FunnelInput) -> dict:
    return db.activation_funnel(active_window_days=inp.days)


def _calls(ctx: ResolvedCtx, inp: CallsInput) -> dict:
    return {"calls": db.list_tool_calls(
        limit=inp.limit, sub=_resolve_sub(inp.sub), tool_name=inp.tool,
        errors_only=inp.errors, since_days=inp.days, org_id=inp.org_id,
        run_id=inp.run_id, session_id=inp.session_id,
        min_duration_ms=inp.min_duration_ms, error_contains=inp.error_contains)}


def _call(ctx: ResolvedCtx, inp: CallInput) -> dict:
    row = db.get_tool_call(inp.call_id)
    if row is None:
        raise AuthzDenied(404, "unknown_call", f"Aucun appel id={inp.call_id}.")
    return {"call": row}


# ── console MCP consolidée `oto_admin_monitoring(op=…)` (pattern ADR 0047) ───

class MonitoringInput(BaseModel):
    op: Literal["summary", "rest", "connectors", "funnel", "calls", "call",
                "runs", "run", "gaps", "tool_quality"]
    days: Optional[int] = None            # fenêtre (défaut : 7 ; funnel/gaps/tool_quality : 30)
    limit: Optional[int] = None           # calls (défaut 200) / runs (défaut 100)
    sub: Optional[str] = None             # summary/calls : filtre appelant (email ou sub)
    tool: Optional[str] = None            # calls : filtre outil exact
    errors: bool = False                  # calls : erreurs seulement
    org_id: Optional[int] = None          # summary/calls : scope un workspace
    run_id: Optional[str] = None          # run (requis) / calls (filtre)
    session_id: Optional[str] = None      # calls : tous les appels d'une conversation
    min_duration_ms: Optional[int] = None  # calls : appels lents
    error_contains: Optional[str] = None  # calls : recherche dans le message d'erreur
    call_id: Optional[int] = None         # call (requis)


def _need(val, code: str, msg: str):
    if val is None or (isinstance(val, str) and not val.strip()):
        raise AuthzDenied(400, code, msg)
    return val


def _monitoring(ctx: ResolvedCtx, inp: MonitoringInput) -> dict:
    if inp.op == "summary":
        return _summary(ctx, SummaryInput(days=inp.days or 7, org_id=inp.org_id, sub=inp.sub))
    if inp.op == "rest":
        return _rest_stats(ctx, WindowInput(days=inp.days or 7))
    if inp.op == "connectors":
        return _connector_stats(ctx, WindowInput(days=inp.days or 7))
    if inp.op == "funnel":
        return _funnel(ctx, FunnelInput(days=inp.days or 30))
    if inp.op == "calls":
        return _calls(ctx, CallsInput(
            limit=inp.limit or 200, sub=inp.sub, tool=inp.tool, errors=inp.errors,
            days=inp.days, org_id=inp.org_id, run_id=inp.run_id,
            session_id=inp.session_id, min_duration_ms=inp.min_duration_ms,
            error_contains=inp.error_contains))
    if inp.op == "call":
        return _call(ctx, CallInput(call_id=_need(
            inp.call_id, "missing_call_id", "`call_id` requis pour call.")))
    if inp.op == "runs":
        return usage._runs(ctx, usage.RunsInput(limit=inp.limit or 100))
    if inp.op == "run":
        return usage._run(ctx, usage.RunInput(run_id=_need(
            inp.run_id, "missing_run_id", "`run_id` requis pour run.")))
    if inp.op == "gaps":
        return usage._gaps(ctx, usage.DaysInput(days=inp.days or 30))
    return usage._tool_quality(ctx, usage.DaysInput(days=inp.days or 30))  # tool_quality


CAPABILITIES += [
    Capability(key="monitoring.summary", handler=_summary, Input=SummaryInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/summary")),
    Capability(key="monitoring.rest", handler=_rest_stats, Input=WindowInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/rest")),
    Capability(key="monitoring.connectors", handler=_connector_stats, Input=WindowInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/connectors")),
    Capability(key="monitoring.funnel", handler=_funnel, Input=FunnelInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/funnel")),
    Capability(key="monitoring.calls", handler=_calls, Input=CallsInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/calls")),
    Capability(key="monitoring.call", handler=_call, Input=CallInput,
               authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/monitoring/calls/{call_id}")),
    Capability(
        key="admin.monitoring", handler=_monitoring, Input=MonitoringInput,
        authz=PLATFORM_ADMIN,
        description=(
            "Platform observability console (platform admin). op=summary (aggregates: "
            "totals, by tool w/ avg+p95 latency, by user, by day; optional `days`, "
            "`org_id`, `sub` email|sub) / calls (raw MCP call log, newest first; filters "
            "`sub`, `tool`, `errors`, `days`, `org_id`, `run_id`, `session_id`, "
            "`min_duration_ms` slow calls, `error_contains`) / call (`call_id` → full "
            "record incl. truncated args + correlation ids) / run (`run_id` → timeline) "
            "/ runs (recent runs) / rest (REST lens by route) / connectors (credential "
            "resolution failures) / funnel (accounts vs real usage) / gaps · tool_quality "
            "(aggregated usage signals). For raw signals use oto_admin_signal."),
        mcp="oto_admin_monitoring",
    ),
]
