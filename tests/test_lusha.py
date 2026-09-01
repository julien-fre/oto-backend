"""lusha_search_and_enrich — one native batch call (up to 100 contacts), NOT
a `_bulk_run`-style loop like folk/checkcrm (Lusha's own endpoint already
accepts an array). Locks: validation (empty/over-cap/unknown reveal value)
happens before any client call, and the tool forwards args through as-is.
"""
import asyncio
from unittest.mock import patch

import pytest
from oto_mcp.mcp_errors import McpError
def _register_and_call(tool_name: str, **kwargs):
    from fastmcp import FastMCP
    from oto_mcp.tools import lusha as lusha_tool

    m = FastMCP("t")
    lusha_tool.register(m)
    fn = asyncio.run(m.get_tool(tool_name)).fn
    return fn(**kwargs)


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False)
    )


@pytest.fixture
def client_cls():
    with patch("oto.tools.lusha.client.LushaClient") as cls:
        yield cls


def _instance(client_cls):
    return client_cls.return_value


def test_forwards_contacts_and_reveal_to_the_client(client_cls):
    inst = _instance(client_cls)
    inst.search_and_enrich.return_value = {"requestId": "r1", "results": [],
                                           "billing": {"creditsCharged": 1}}
    r = _register_and_call(
        "lusha_search_and_enrich",
        contacts=[{"email": "jane.doe@acme.test"}],
        reveal=["emails", "phones"])
    inst.search_and_enrich.assert_called_once_with(
        [{"email": "jane.doe@acme.test"}],
        reveal=["emails", "phones"], include_partial_profiles=None)
    assert r == {"requestId": "r1", "results": [], "billing": {"creditsCharged": 1}}


def test_reveal_and_include_partial_profiles_default_to_none(client_cls):
    inst = _instance(client_cls)
    inst.search_and_enrich.return_value = {"results": []}
    _register_and_call("lusha_search_and_enrich", contacts=[{"email": "a@b.com"}])
    inst.search_and_enrich.assert_called_once_with(
        [{"email": "a@b.com"}], reveal=None, include_partial_profiles=None)


def test_rejects_empty_contacts_before_any_call(client_cls):
    inst = _instance(client_cls)
    with pytest.raises(McpError, match="au moins un"):
        _register_and_call("lusha_search_and_enrich", contacts=[])
    inst.search_and_enrich.assert_not_called()


def test_rejects_over_100_contacts_before_any_call(client_cls):
    inst = _instance(client_cls)
    with pytest.raises(McpError, match="100"):
        _register_and_call(
            "lusha_search_and_enrich",
            contacts=[{"email": f"{i}@b.com"} for i in range(101)])
    inst.search_and_enrich.assert_not_called()


def test_exactly_100_contacts_is_accepted(client_cls):
    inst = _instance(client_cls)
    inst.search_and_enrich.return_value = {"results": []}
    _register_and_call(
        "lusha_search_and_enrich",
        contacts=[{"email": f"{i}@b.com"} for i in range(100)])
    inst.search_and_enrich.assert_called_once()


def test_rejects_unknown_reveal_value_before_any_call(client_cls):
    inst = _instance(client_cls)
    with pytest.raises(McpError, match="phone_numbers"):
        _register_and_call(
            "lusha_search_and_enrich",
            contacts=[{"email": "a@b.com"}], reveal=["phone_numbers"])
    inst.search_and_enrich.assert_not_called()


def test_tool_has_a_real_docstring():
    # Garde le piège f-string docstring (le skill oto-add-tool) : un
    # `f"""..."""` ne peuplerait PAS `__doc__`, et FastMCP livrerait le tool
    # sans description, sans erreur.
    from fastmcp import FastMCP
    from oto_mcp.tools import lusha as lusha_tool

    m = FastMCP("t")
    lusha_tool.register(m)
    tool = asyncio.run(m.get_tool("lusha_search_and_enrich"))
    assert tool.description
    assert "100 contacts" in tool.description
