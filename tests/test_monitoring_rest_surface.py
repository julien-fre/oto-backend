"""Surface REST des lentilles monitoring, exercée par l'ADAPTATEUR réel.

Le risque de la migration routes-écrites-main → capacités n'est pas le handler
(testé ailleurs) mais le TRANSPORT : les filtres arrivent en query string, donc en
`str`, et doivent être coercés par l'Input pydantic (`errors=1` → bool,
`min_duration_ms=5000` → int). Un test qui appellerait le handler avec des types
Python déjà propres déclarerait vert un chemin cassé — on monte donc les vraies
routes via `_rest_adapter.make_routes` et on tape dessus.
"""
import asyncio

import pytest

from oto_mcp import db
from oto_mcp.capabilities import _authz, _rest_adapter
from oto_mcp.capabilities.registry import CAPABILITIES


class FakeReq:
    def __init__(self, query=None, path_params=None, method="GET"):
        self.query_params = query or {}
        self.path_params = path_params or {}
        self.method = method

    async def json(self):
        return {}


def _mount(monkeypatch, *, admin=True):
    """Routes des seules capacités monitoring. On garde la VRAIE règle d'autz
    (PLATFORM_ADMIN) et on stubbe l'opérateur plateforme dessous — la capacité est
    frozen, et un stub de règle masquerait un palier mal déclaré."""
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda s: admin)
    # La règle enrichit le ctx (org active + rôle) → sans DB dans ce test.
    monkeypatch.setattr(_authz.access, "current_org", lambda s: None)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda s: "admin")

    async def authenticate(request, verifier):
        return ("admin-sub", None)

    def json_response(request, payload):
        return payload

    def json_error(request, status, code, detail=None):
        return {"_status": status, "_code": code, "_detail": detail}

    async def options_handler(request):
        return "opt"

    caps = [c for c in CAPABILITIES if c.key.startswith("monitoring.")]
    routes = _rest_adapter.make_routes(
        None, authenticate, json_response, json_error, options_handler, caps)
    return {(r.path, m): r.endpoint for r in routes for m in r.methods}


def test_non_admin_is_refused(monkeypatch):
    """Le palier ne s'est pas perdu dans la migration : sans opérateur → 403."""
    monkeypatch.setattr(db, "list_tool_calls", lambda **kw: [])
    idx = _mount(monkeypatch, admin=False)
    out = asyncio.run(idx[("/api/admin/monitoring/calls", "GET")](FakeReq()))
    assert out["_status"] == 403


def test_query_string_filters_are_coerced(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "list_tool_calls", lambda **kw: seen.update(kw) or [])
    idx = _mount(monkeypatch)
    handler = idx[("/api/admin/monitoring/calls", "GET")]

    asyncio.run(handler(FakeReq(query={
        "days": "7", "limit": "50", "errors": "1",
        "min_duration_ms": "5000", "error_contains": "timeout", "tool": "folk_record",
    })))

    # Les valeurs arrivent typées, pas en str — c'est ce que le SQL attend.
    assert seen["since_days"] == 7 and isinstance(seen["since_days"], int)
    assert seen["limit"] == 50 and isinstance(seen["limit"], int)
    assert seen["errors_only"] is True
    assert seen["min_duration_ms"] == 5000 and isinstance(seen["min_duration_ms"], int)
    assert seen["error_contains"] == "timeout"


def test_call_detail_route_reads_path_param(monkeypatch):
    monkeypatch.setattr(db, "get_tool_call", lambda cid: {"id": cid, "tool": "fr_get"})
    idx = _mount(monkeypatch)
    handler = idx[("/api/admin/monitoring/calls/{call_id}", "GET")]
    out = asyncio.run(handler(FakeReq(path_params={"call_id": "42"})))
    assert out == {"call": {"id": 42, "tool": "fr_get"}}


def test_unknown_call_is_a_404_not_a_500(monkeypatch):
    monkeypatch.setattr(db, "get_tool_call", lambda cid: None)
    idx = _mount(monkeypatch)
    handler = idx[("/api/admin/monitoring/calls/{call_id}", "GET")]
    out = asyncio.run(handler(FakeReq(path_params={"call_id": "999"})))
    assert out["_status"] == 404 and out["_code"] == "unknown_call"


@pytest.mark.parametrize("path,fn,key", [
    ("/api/admin/monitoring/summary", "tool_call_stats", "since_days"),
    ("/api/admin/monitoring/rest", "rest_call_stats", "since_days"),
    ("/api/admin/monitoring/connectors", "connector_failure_stats", "since_days"),
])
def test_windowed_lenses_pass_days(monkeypatch, path, fn, key):
    seen = {}
    monkeypatch.setattr(db, fn, lambda **kw: seen.update(kw) or {})
    idx = _mount(monkeypatch)
    asyncio.run(idx[(path, "GET")](FakeReq(query={"days": "30"})))
    assert seen[key] == 30


def test_funnel_defaults_to_30_days(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "activation_funnel", lambda **kw: seen.update(kw) or {})
    idx = _mount(monkeypatch)
    asyncio.run(idx[("/api/admin/monitoring/funnel", "GET")](FakeReq()))
    assert seen["active_window_days"] == 30
