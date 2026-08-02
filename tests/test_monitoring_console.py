"""Console d'investigation `oto_admin_monitoring` (capabilities/monitoring.py).

Logique pure : dispatch op→handler (stubs db), défauts de fenêtre par op,
paramètres requis (`call_id`, `run_id`), résolution email→sub du filtre appelant,
et fiche d'appel introuvable → AuthzDenied 404 (jamais un 500).
"""
import pytest

from oto_mcp.capabilities import monitoring, usage
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="admin-sub")


def test_summary_defaults_and_sub_resolution(monkeypatch):
    seen = {}
    monkeypatch.setattr(monitoring.db, "tool_call_stats",
                        lambda since_days, org_id, sub: seen.update(
                            days=since_days, org_id=org_id, sub=sub) or {"ok": 1})
    monkeypatch.setattr(monitoring, "_resolve_sub", lambda t: "sub-jb" if t else None)
    out = monitoring._monitoring(CTX, monitoring.MonitoringInput(
        op="summary", sub="jb@example.com"))
    assert out == {"ok": 1}
    assert seen == {"days": 7, "org_id": None, "sub": "sub-jb"}


def test_calls_passes_investigation_filters(monkeypatch):
    seen = {}
    monkeypatch.setattr(monitoring.db, "list_tool_calls",
                        lambda **kw: seen.update(kw) or [])
    monitoring._monitoring(CTX, monitoring.MonitoringInput(
        op="calls", run_id="r1", session_id="s1", min_duration_ms=5000,
        error_contains="timeout", errors=True, tool="folk_search"))
    assert seen["run_id"] == "r1"
    assert seen["session_id"] == "s1"
    assert seen["min_duration_ms"] == 5000
    assert seen["error_contains"] == "timeout"
    assert seen["errors_only"] is True
    assert seen["tool_name"] == "folk_search"
    assert seen["limit"] == 200          # défaut console


def test_call_requires_id_and_404s_on_unknown(monkeypatch):
    with pytest.raises(AuthzDenied) as e:
        monitoring._monitoring(CTX, monitoring.MonitoringInput(op="call"))
    assert e.value.code == "missing_call_id"
    monkeypatch.setattr(monitoring.db, "get_tool_call", lambda cid: None)
    with pytest.raises(AuthzDenied) as e:
        monitoring._monitoring(CTX, monitoring.MonitoringInput(op="call", call_id=999))
    assert e.value.status == 404


def test_run_requires_run_id():
    with pytest.raises(AuthzDenied) as e:
        monitoring._monitoring(CTX, monitoring.MonitoringInput(op="run"))
    assert e.value.code == "missing_run_id"


def test_rest_paths_are_unchanged_for_the_dashboard():
    """Les lentilles ont quitté `api_routes` pour la couche capacité : les CHEMINS
    doivent rester identiques (le dashboard tape ces URLs, cf. api/console.ts) —
    une migration interne ne doit jamais casser une surface consommée."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    paths = {b.path for c in CAPABILITIES if c.key.startswith("monitoring.")
             for b in c.rest_bindings()}
    assert paths == {
        "/api/admin/monitoring/summary",
        "/api/admin/monitoring/rest",
        "/api/admin/monitoring/connectors",
        "/api/admin/monitoring/funnel",
        "/api/admin/monitoring/calls",
        "/api/admin/monitoring/calls/{call_id}",
    }


def test_usage_ops_reuse_adr0017_handlers(monkeypatch):
    seen = {}
    monkeypatch.setattr(usage.db, "aggregate_gaps", lambda days: seen.update(gaps=days) or [])
    monkeypatch.setattr(usage.db, "list_runs", lambda limit: seen.update(runs=limit) or [])
    monitoring._monitoring(CTX, monitoring.MonitoringInput(op="gaps"))
    monitoring._monitoring(CTX, monitoring.MonitoringInput(op="runs"))
    assert seen == {"gaps": 30, "runs": 100}   # défauts par op (gaps 30j, runs 100)
