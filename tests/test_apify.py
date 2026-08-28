"""Connecteur Apify — actors de scraping hébergés (api.apify.com).

Verrouille : l'entrée de registre (keyed byo-only, catégorie Prospection), la
surface MCP sous le namespace `apify`, la jointure tool↔client oto-core (garde
version-skew), la sonde « tester la connexion », et les deux endroits où le module
protège l'utilisateur : le 408 du mode synchrone doit ORIENTER vers le mode
asynchrone, et les garde-fous de coût doivent atteindre le client.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp import providers
from oto_mcp.connectors import verify as connector_verify
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "apify_store_search", "apify_actors", "apify_actor",
    "apify_run_sync", "apify_run", "apify_run_status", "apify_abort_run",
    "apify_dataset_items",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name for t in asyncio.run(m._list_tools())}


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import apify

    m = FastMCP("t")
    apify.register(m)
    return asyncio.run(m.get_tool(name))


# --- registre -----------------------------------------------------------------

def test_apify_is_keyed_connector_platform_grant_only():
    c = providers.REGISTRY["apify"]
    assert c.kind == "tools"
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org", "platform"})
    # platform sur grant explicite (revente au crédit) : jamais en libre-service.
    assert c.platform_key_open is False
    assert c.default_quota == 0
    assert "apify" in providers.KEY_PROVIDERS
    assert c.category == "Prospection"
    assert c.publisher_name == "Apify"


def test_apify_has_onboarding_doc():
    kinds = {s.kind for s in providers.REGISTRY["apify"].doc_sections}
    assert {"prerequisite", "usage"} <= kinds


# --- surface MCP --------------------------------------------------------------

def test_apify_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= all_tools
    assert all(namespace_of(t) == "apify"
               for t in all_tools if t.startswith("apify_"))


def test_verify_probe_registered():
    assert connector_verify.supports("apify")


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.apify.client import ApifyClient
    for meth in ("store_search", "actors", "actor", "run_sync_dataset_items",
                 "run", "run_status", "abort_run", "dataset_items"):
        assert callable(getattr(ApifyClient, meth, None)), f"ApifyClient.{meth} manquant"


# --- garde-fous de coût & orientation sur 408 ---------------------------------

def _with_fake_client():
    key = patch("oto_mcp.access.resolve_api_key", return_value=("fake-key", False))
    cls = patch("oto.tools.apify.client.ApifyClient")
    return key, cls


def test_run_sync_forwards_cost_guards():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        _tool("apify_run_sync").fn(
            actor_id="apify/google-maps-scraper",
            run_input={"searchStringsArray": ["boulangerie Marseille"]},
            max_items=20, max_total_charge_usd=1.5,
        )

        kwargs = inst.run_sync_dataset_items.call_args.kwargs
        assert kwargs["max_items"] == 20
        assert kwargs["max_total_charge_usd"] == 1.5


def test_sync_timeout_points_to_the_async_path():
    """408 = le run dépasse 300 s : le message doit nommer la sortie de secours."""
    from mcp.shared.exceptions import McpError
    from oto.tools.common.errors import UpstreamHTTPError

    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.run_sync_dataset_items.side_effect = UpstreamHTTPError(
            408, {"error": "timeout"}, service="apify")
        with pytest.raises(McpError, match="apify_run_status"):
            _tool("apify_run_sync").fn(actor_id="apify/x")
