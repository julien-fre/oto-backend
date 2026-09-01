"""Export du journal d'audit org-scopé (oto-backend#67).

Vérifie le routage du handler (org → appels, namespace dérivé) et le câblage de la
capacité (REST-only, gatée ORG_ADMIN_OF). Ce que la réponse dit de sa propre
complétude — `total`, `truncated`, `next_cursor`, `until_effectif` — vit dans
`test_audit_log_completude_770.py`, avec sa garde contre un vrai PostgreSQL.
"""
from oto_mcp.capabilities import audit_log as al
from oto_mcp.capabilities import registry
from oto_mcp.capabilities._types import ResolvedCtx

CTX = ResolvedCtx(sub="admin", org_id=7)

_GEL = "2026-09-01T14:00:00.000000Z"


def test_export_scopes_to_call_org_and_derives_namespace(monkeypatch):
    captured = {}

    def fake_read(org_id, since=None, until=None, limit=1000, before=None):
        captured.update(org_id=org_id, since=since, until=until, limit=limit,
                        before=before)
        return {"until_effectif": _GEL, "total": 2, "next": None,
                "calls": [{"tool": "fr_get", "ok": True},
                          {"tool": "oto_admin_org", "ok": True}]}

    monkeypatch.setattr(al.db, "export_tool_calls_for_org", fake_read)
    out = al._export(CTX, al.AuditExportInput(org_id=7, since="2026-06-01", limit=500))

    # filtre EXACT par org de l'appel (pas l'appartenance des membres)
    assert captured["org_id"] == 7
    assert captured["since"] == "2026-06-01" and captured["limit"] == 500
    assert out["org_id"] == 7 and out["count"] == 2
    assert [c["namespace"] for c in out["calls"]] == ["fr", "oto"]


def test_empty_yields_zero(monkeypatch):
    monkeypatch.setattr(al.db, "export_tool_calls_for_org",
                        lambda org_id, **k: {"until_effectif": _GEL, "total": 0,
                                             "calls": [], "next": None})
    out = al._export(CTX, al.AuditExportInput(org_id=7))
    assert out["count"] == 0 and out["calls"] == []


def test_capability_is_rest_only_org_admin_gated():
    cap = next(c for c in registry.CAPABILITIES if c.key == "org.audit_log.export")
    assert cap.mcp is None                                   # REST-only
    b = cap.rest_bindings()[0]
    assert b.verb == "GET" and b.path == "/api/orgs/{id}/audit-log/export"
    # ORG_ADMIN_OF est une règle paramétrée (closure) — présence + appelable.
    assert callable(cap.authz)
