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
    monkeypatch.setattr(cap.db, "list_org_disabled_tools", lambda oid: ["attio_record"])
    out = cap._org_list(_ctx(), cap.OrgHiddenToolsListInput(org_id=42))
    assert out == {"org_id": 42, "disabled_tools": ["attio_record"]}


def test_org_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    with pytest.raises(AuthzDenied) as e:
        cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_org_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    calls = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool",
                        lambda org_id, name, disabled_by=None: calls.append((org_id, name, disabled_by)))
    out = cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_record"))
    assert out == {"org_id": 42, "tool": "attio_record", "hidden": True}
    assert calls == [(42, "attio_record", "admin")]


def test_org_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool",
                        lambda org_id, name: calls.append((org_id, name)))
    out = cap._org_unhide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_record"))
    assert out == {"org_id": 42, "tool": "attio_record", "hidden": False}
    assert calls == [(42, "attio_record")]


# ── équipe ───────────────────────────────────────────────────────────────────

def test_group_list_unknown_group_raises(monkeypatch):
    monkeypatch.setattr(cap.group_store, "get_group", lambda gid: None)
    with pytest.raises(AuthzDenied) as e:
        cap._group_list(_ctx(), cap.GroupHiddenToolsListInput(group_id=7))
    assert e.value.code == "unknown_group"


def test_group_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    with pytest.raises(AuthzDenied) as e:
        cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_group_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    calls = []
    monkeypatch.setattr(cap.db, "add_group_disabled_tool",
                        lambda group_id, name, disabled_by=None: calls.append((group_id, name, disabled_by)))
    out = cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_record"))
    assert out == {"group_id": 7, "tool": "attio_record", "hidden": True}
    assert calls == [(7, "attio_record", "admin")]


def test_group_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_group_disabled_tool",
                        lambda group_id, name: calls.append((group_id, name)))
    out = cap._group_unhide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_record"))
    assert out == {"group_id": 7, "tool": "attio_record", "hidden": False}
    assert calls == [(7, "attio_record")]


# --- démasquer accepte n'importe quel nom : échappatoire ASSUMÉE (#293) -------
#
# Masquer valide (404 inconnu, 400 protégé), démasquer non. C'est ce qui permet de
# nettoyer une ligne dont le tool a été renommé ou retiré — rien d'autre ne le fait.
# Une purge automatique n'a pas de référentiel fiable : `boot_tool_names()` ne liste
# que ce qui a été MONTÉ (un module dont une dép manque est désactivé en silence, et
# le registre non réchauffé rend `[]`), donc elle effacerait de la gouvernance vivante
# au premier import raté. Ces deux tests figent la porte ouverte ET sa mention dans le
# contrat publié : la refermer sans écrire la purge doit les casser.

def test_demasquer_accepte_un_nom_que_masquer_refuserait(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    retires = []
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool",
                        lambda oid, name: retires.append((oid, name)))
    monkeypatch.setattr(cap.db, "remove_group_disabled_tool",
                        lambda gid, name: retires.append((gid, name)))

    # `tool_disparu` : inconnu du registre — le cas du tool renommé/retiré.
    # `oto_whoami` : protégé — masquer le refuse, démasquer doit rester possible.
    assert cap._org_unhide(
        _ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="tool_disparu"))["hidden"] is False
    assert cap._group_unhide(
        _ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="oto_whoami"))["hidden"] is False
    assert retires == [(42, "tool_disparu"), (7, "oto_whoami")]


def test_le_contrat_publie_annonce_lechappatoire():
    """Un effet de bord non écrit est une dette ; écrit dans la `description=`, c'est
    une décision que l'intégrateur lit dans `/api/openapi.json` et le schéma MCP."""
    from oto_mcp.capabilities import registry

    for key in ("tools.org_unhide", "tools.group_unhide"):
        d = next(c for c in registry.CAPABILITIES if c.key == key).description or ""
        assert "the hide side would refuse" in d and "stale row" in d, (
            f"{key} ne documente plus qu'il accepte un nom inconnu : soit tu le "
            "redis, soit tu valides des deux côtés ET tu écris la purge.")


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
