"""Connecteur Snitcher — identification des visiteurs du site web
(api.snitcher.com/v1).

Verrouille : l'entrée de registre (keyed byo-only, catégorie Prospection), la
doc how-to, la surface MCP (5 tools — `snitcher_workspace`,
`snitcher_organisation`, `snitcher_contact`, `snitcher_session`,
`snitcher_custom_field` — chacun avec une description, régression du piège
f-string-docstring), la sonde « tester la connexion », la jointure
tool↔client oto-core (garde version-skew), et le dispatch `op=` (required
manquant refusé, arg non pertinent pour CET op refusé, exclusions
date/plage et organisation_uuid/domain).
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import connector_verify, providers
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools import snitcher

EXPECTED_TOOLS = {
    "snitcher_workspace", "snitcher_organisation", "snitcher_contact",
    "snitcher_session", "snitcher_custom_field",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False))


def _fn_with_mock_client():
    """Enregistre le module avec `SnitcherClient` mocké, DANS le patch (sinon
    `register()`'s `from ... import SnitcherClient` capture la vraie classe
    avant que le patch ne s'applique)."""
    from fastmcp import FastMCP

    patcher = patch("oto.tools.snitcher.client.SnitcherClient")
    cls = patcher.start()
    m = FastMCP("t")
    snitcher.register(m)
    return m, cls, patcher


# --- registre -----------------------------------------------------------------

def test_snitcher_is_keyed_byo_only_connector():
    c = providers.REGISTRY["snitcher"]
    assert c.kind == "tools"
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "platform" not in c.auth_modes
    assert c.default_active is False
    assert c.default_quota == 0
    assert "snitcher" in providers.KEY_PROVIDERS
    assert c.category == "Prospection"
    assert c.publisher_name == "Snitcher"
    assert c.label == "Snitcher"
    assert providers._LOGO_DOMAIN_BY_CONNECTOR["snitcher"] == "snitcher.com"
    assert [f.name for f in c.credential_fields] == ["key"]


def test_snitcher_has_onboarding_doc():
    kinds = {s.kind for s in providers.REGISTRY["snitcher"].doc_sections}
    assert {"prerequisite", "usage"} <= kinds


# --- surface MCP --------------------------------------------------------------

def test_snitcher_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= set(all_tools)
    assert all(namespace_of(t) == "snitcher" for t in all_tools if t.startswith("snitcher_"))


def test_snitcher_tools_all_have_descriptions(all_tools):
    for name in EXPECTED_TOOLS:
        assert all_tools[name].description, f"{name} has no description"


def test_verify_probe_registered():
    _fn_with_mock_client()
    assert connector_verify.supports("snitcher")


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.snitcher.client import SnitcherClient
    for meth in ("get_me", "list_workspaces", "create_workspace", "get_workspace",
                 "update_workspace", "delete_workspace", "invite_user",
                 "create_workspace_tag", "list_organisations", "filter_organisations",
                 "get_organisation", "add_organisation_tag", "remove_organisation_tag",
                 "list_contacts", "reveal_contact_email", "list_sessions",
                 "list_organisation_sessions", "list_segments", "list_custom_fields",
                 "create_custom_field", "get_custom_field", "update_custom_field",
                 "delete_custom_field", "list_custom_field_values",
                 "set_custom_field_values", "set_custom_field_value",
                 "clear_custom_field_value"):
        assert callable(getattr(SnitcherClient, meth, None)), f"SnitcherClient.{meth} manquant"


# --- dispatch op= : snitcher_workspace ---------------------------------------

def test_workspace_list_refuses_target_params():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_workspace")).fn
        with pytest.raises(McpError, match="op='list' n'utilise pas"):
            fn(op="list", workspace_uuid="ws_1")
        cls.return_value.list_workspaces.assert_not_called()

        cls.return_value.list_workspaces.return_value = {"data": []}
        fn(op="list", page=2, size=50)
        cls.return_value.list_workspaces.assert_called_once_with(page=2, size=50)
    finally:
        patcher.stop()


def test_workspace_me_and_segments():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_workspace")).fn
        cls.return_value.get_me.return_value = {"data": {"name": "J"}}
        assert fn(op="me") == {"data": {"name": "J"}}

        with pytest.raises(McpError, match="requiert .workspace_uuid."):
            fn(op="segments")
        cls.return_value.list_segments.return_value = {"data": []}
        fn(op="segments", workspace_uuid="ws_1")
        cls.return_value.list_segments.assert_called_once_with("ws_1")
    finally:
        patcher.stop()


def test_workspace_create_update_invite_create_tag_required_params():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_workspace")).fn
        with pytest.raises(McpError, match="requiert .url."):
            fn(op="create")
        with pytest.raises(McpError, match="requiert .usage_limit."):
            fn(op="update", workspace_uuid="ws_1")
        with pytest.raises(McpError, match="requiert .email."):
            fn(op="invite", workspace_uuid="ws_1")
        with pytest.raises(McpError, match="requiert .tag_name."):
            fn(op="create_tag", workspace_uuid="ws_1")

        cls.return_value.create_workspace.return_value = {"data": {"uuid": "ws_new"}}
        fn(op="create", url="https://example.com")
        cls.return_value.create_workspace.assert_called_once_with("https://example.com")

        cls.return_value.delete_workspace.return_value = None
        fn(op="delete", workspace_uuid="ws_1")
        cls.return_value.delete_workspace.assert_called_once_with("ws_1")
    finally:
        patcher.stop()


# --- dispatch op= : snitcher_organisation ------------------------------------

def test_organisation_list_date_exclusivity_and_search():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_organisation")).fn
        with pytest.raises(McpError, match="mutuellement exclusifs"):
            fn(workspace_uuid="ws_1", op="list", date="2026-08-01", date_from="2026-07-01")

        with pytest.raises(McpError, match="requiert .filters."):
            fn(workspace_uuid="ws_1", op="search")

        filters = {"operator": "AND", "conditions": [
            {"field": "employees", "comparison": "greater_than", "value": 200}]}
        cls.return_value.filter_organisations.return_value = {"data": []}
        fn(workspace_uuid="ws_1", op="search", filters=filters, size=100)
        cls.return_value.filter_organisations.assert_called_once_with(
            "ws_1", filters, segment_uuid=None, page=None, size=100)
    finally:
        patcher.stop()


def test_organisation_get_tag_untag():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_organisation")).fn
        with pytest.raises(McpError, match="requiert .organisation_uuid."):
            fn(workspace_uuid="ws_1", op="get")
        with pytest.raises(McpError, match="requiert .tag_name."):
            fn(workspace_uuid="ws_1", op="tag", organisation_uuid="org_1")

        cls.return_value.add_organisation_tag.return_value = None
        fn(workspace_uuid="ws_1", op="tag", organisation_uuid="org_1", tag_name="hot")
        cls.return_value.add_organisation_tag.assert_called_once_with("ws_1", "org_1", "hot")

        cls.return_value.remove_organisation_tag.return_value = None
        fn(workspace_uuid="ws_1", op="untag", organisation_uuid="org_1", tag_name="hot")
        cls.return_value.remove_organisation_tag.assert_called_once_with("ws_1", "org_1", "hot")
    finally:
        patcher.stop()


# --- dispatch : snitcher_contact ---------------------------------------------

def test_contact_list_requires_exactly_one_of_org_domain():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_contact")).fn
        with pytest.raises(McpError, match="EXACTEMENT UN"):
            fn(workspace_uuid="ws_1", op="list")
        with pytest.raises(McpError, match="EXACTEMENT UN"):
            fn(workspace_uuid="ws_1", op="list", organisation_uuid="org_1", domain="acme.com")

        cls.return_value.list_contacts.return_value = {"data": []}
        fn(workspace_uuid="ws_1", op="list", domain="acme.com")
        cls.return_value.list_contacts.assert_called_once_with(
            "ws_1", organisation_uuid=None, domain="acme.com", page=None, size=None)
    finally:
        patcher.stop()


def test_contact_reveal_email_requires_contact_uuid():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_contact")).fn
        with pytest.raises(McpError, match="requiert .contact_uuid."):
            fn(workspace_uuid="ws_1", op="reveal_email")
        with pytest.raises(McpError, match="n'utilise pas"):
            fn(workspace_uuid="ws_1", op="reveal_email", contact_uuid="c_1", domain="acme.com")

        cls.return_value.reveal_contact_email.return_value = {"data": {"email": "j@acme.com"}}
        fn(workspace_uuid="ws_1", op="reveal_email", contact_uuid="c_1")
        cls.return_value.reveal_contact_email.assert_called_once_with("ws_1", "c_1")
    finally:
        patcher.stop()


# --- dispatch : snitcher_session ---------------------------------------------

def test_session_routes_to_org_or_workspace_endpoint():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_session")).fn
        with pytest.raises(McpError, match="mutuellement exclusifs"):
            fn(workspace_uuid="ws_1", date="2026-08-01", date_to="2026-08-10")
        with pytest.raises(McpError, match="workspace-wide"):
            fn(workspace_uuid="ws_1", organisation_uuid="org_1", segment_uuid="seg_1")

        cls.return_value.list_organisation_sessions.return_value = {"data": []}
        fn(workspace_uuid="ws_1", organisation_uuid="org_1")
        cls.return_value.list_organisation_sessions.assert_called_once()

        cls.return_value.list_sessions.return_value = {"data": []}
        fn(workspace_uuid="ws_1", date="2026-08-01")
        cls.return_value.list_sessions.assert_called_once()
    finally:
        patcher.stop()


# --- dispatch : snitcher_custom_field ----------------------------------------

def test_custom_field_definition_ops():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_custom_field")).fn
        with pytest.raises(McpError, match="requiert .name. et .type."):
            fn(workspace_uuid="ws_1", op="create")
        with pytest.raises(McpError, match="requiert .key."):
            fn(workspace_uuid="ws_1", op="get")
        with pytest.raises(McpError, match="immuable"):
            fn(workspace_uuid="ws_1", op="update", key="tier", type="text")

        cls.return_value.create_custom_field.return_value = {"data": {"key": "industry"}}
        fn(workspace_uuid="ws_1", op="create", name="Industry", type="text")
        cls.return_value.create_custom_field.assert_called_once_with(
            "ws_1", "Industry", "text", key=None, description=None,
            visible_in_spotter=None, field_rules=None, options=None)
    finally:
        patcher.stop()


def test_custom_field_value_ops_require_organisation():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = asyncio.run(m.get_tool("snitcher_custom_field")).fn
        with pytest.raises(McpError, match="requiert .organisation_uuid."):
            fn(workspace_uuid="ws_1", op="values")
        with pytest.raises(McpError, match="requiert .key. et .value."):
            fn(workspace_uuid="ws_1", op="set", organisation_uuid="org_1")
        with pytest.raises(McpError, match="requiert .values."):
            fn(workspace_uuid="ws_1", op="set_many", organisation_uuid="org_1")

        cls.return_value.set_custom_field_value.return_value = {"data": {}}
        fn(workspace_uuid="ws_1", op="set", organisation_uuid="org_1",
           key="account_tier", value="enterprise")
        cls.return_value.set_custom_field_value.assert_called_once_with(
            "ws_1", "org_1", "account_tier", "enterprise")

        cls.return_value.set_custom_field_values.return_value = {"data": {}}
        fn(workspace_uuid="ws_1", op="set_many", organisation_uuid="org_1",
           values={"deal_size": 50000})
        cls.return_value.set_custom_field_values.assert_called_once_with(
            "ws_1", "org_1", {"deal_size": 50000})

        cls.return_value.clear_custom_field_value.return_value = None
        fn(workspace_uuid="ws_1", op="clear", organisation_uuid="org_1", key="account_tier")
        cls.return_value.clear_custom_field_value.assert_called_once_with(
            "ws_1", "org_1", "account_tier")
    finally:
        patcher.stop()
