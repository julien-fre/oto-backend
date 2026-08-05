"""Denylist de tools par org/équipe (remplace l'ancienne baseline allowlist,
retirée 2026-07-03 commit 3951a57). Gardes de capacité testées par stub (pas de
DB) — même convention que test_group_connector_activation.py.
"""
from types import SimpleNamespace

import pytest

from oto_mcp.capabilities import tools_visibility as cap
from oto_mcp.capabilities._types import AuthzDenied


def _ctx():
    return SimpleNamespace(sub="admin")


# ── org ──────────────────────────────────────────────────────────────────────

def test_org_list_unknown_org_raises(monkeypatch):
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: None)
    with pytest.raises(AuthzDenied) as e:
        cap._org_list(_ctx(), cap.OrgHiddenToolsListInput(org_id=42))
    assert e.value.code == "unknown_org"


def test_org_list_returns_stored_names(monkeypatch):
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: {"id": oid})
    monkeypatch.setattr(cap.db, "list_org_disabled_tools", lambda oid: ["attio_delete_deal"])
    out = cap._org_list(_ctx(), cap.OrgHiddenToolsListInput(org_id=42))
    assert out == {"org_id": 42, "disabled_tools": ["attio_delete_deal"]}


def test_org_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_create_deal"])
    with pytest.raises(AuthzDenied) as e:
        cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_org_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_delete_deal"])
    calls = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool",
                        lambda org_id, name, disabled_by=None: calls.append((org_id, name, disabled_by)))
    out = cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_delete_deal"))
    assert out == {"org_id": 42, "tool": "attio_delete_deal", "hidden": True}
    assert calls == [(42, "attio_delete_deal", "admin")]


def test_org_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool",
                        lambda org_id, name: calls.append((org_id, name)))
    out = cap._org_unhide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_delete_deal"))
    assert out == {"org_id": 42, "tool": "attio_delete_deal", "hidden": False}
    assert calls == [(42, "attio_delete_deal")]


# ── équipe ───────────────────────────────────────────────────────────────────

def test_group_list_unknown_group_raises(monkeypatch):
    monkeypatch.setattr(cap.group_store, "get_group", lambda gid: None)
    with pytest.raises(AuthzDenied) as e:
        cap._group_list(_ctx(), cap.GroupHiddenToolsListInput(group_id=7))
    assert e.value.code == "unknown_group"


def test_group_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_create_deal"])
    with pytest.raises(AuthzDenied) as e:
        cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_group_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_delete_deal"])
    calls = []
    monkeypatch.setattr(cap.db, "add_group_disabled_tool",
                        lambda group_id, name, disabled_by=None: calls.append((group_id, name, disabled_by)))
    out = cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_delete_deal"))
    assert out == {"group_id": 7, "tool": "attio_delete_deal", "hidden": True}
    assert calls == [(7, "attio_delete_deal", "admin")]


def test_group_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_group_disabled_tool",
                        lambda group_id, name: calls.append((group_id, name)))
    out = cap._group_unhide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_delete_deal"))
    assert out == {"group_id": 7, "tool": "attio_delete_deal", "hidden": False}
    assert calls == [(7, "attio_delete_deal")]


# --- outils protégés : refus à l'ÉCRITURE, pas seulement à la lecture ---------
#
# `is_tool_visible` ignore déjà le denylist sur un tool protégé — donc sans refus
# ici, l'admin recevrait `hidden: true` sur un masquage qui ne masque rien. Les
# deux autres faces du geste refusent (`oto_disable_tool`, `POST /api/me/tools/
# {name}` → 400 protected_tool) ; ces deux tests figent l'alignement.

def test_org_hide_refuses_protected_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["oto_whoami"])
    stored = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool",
                        lambda *a, **k: stored.append(a))

    with pytest.raises(AuthzDenied) as e:
        cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=2, name="oto_whoami"))

    assert e.value.code == "protected_tool"
    assert not stored, "aucune ligne ne doit être écrite pour un tool protégé"


def test_group_hide_refuses_protected_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["oto_call"])
    stored = []
    monkeypatch.setattr(cap.db, "add_group_disabled_tool",
                        lambda *a, **k: stored.append(a))

    with pytest.raises(AuthzDenied) as e:
        cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="oto_call"))

    assert e.value.code == "protected_tool"
    assert not stored
