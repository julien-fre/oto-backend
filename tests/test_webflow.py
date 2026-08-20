"""Connecteur Webflow — verrouille : l'entrée registre (fields, PAS keyed — un
Site API token Webflow est bound à UN site, `site_id` voyage comme champ
NON-secret du même credential plutôt qu'en param d'appel), la surface MCP (4
tools), et la jointure tool <-> client oto-core (garde version-skew).
"""
import asyncio

import pytest

from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "webflow_site",
    "webflow_collections",
    "webflow_items",
    "webflow_publish",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    tools = asyncio.run(m._list_tools())
    return {t.name: t for t in tools}


# --- registre -------------------------------------------------------------

def test_webflow_is_fields_connector_not_keyed():
    # Un Site API token Webflow est bound à UN site (Site tokens are created
    # per site) : site_id voyage AVEC le token comme champ non-secret du même
    # credential -> secret_kind="fields" + resolve_credential_fields, pas
    # keyed/resolve_api_key.
    c = providers.REGISTRY["webflow"]
    assert c.kind == "tools"
    assert c.mount_url is None
    assert c.keyed is False
    assert c.secret_kind == "fields"
    assert "webflow" not in providers.KEY_PROVIDERS


def test_webflow_is_byo_only_no_platform_mode():
    c = providers.REGISTRY["webflow"]
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "platform" not in c.auth_modes


def test_webflow_credential_fields_shape():
    c = providers.REGISTRY["webflow"]
    fields = {f.name: f for f in c.credential_fields}
    assert set(fields) == {"token", "site_id"}
    assert fields["token"].secret is True
    assert fields["site_id"].secret is False


def test_webflow_deny_by_default():
    c = providers.REGISTRY["webflow"]
    assert c.default_active is False


def test_webflow_no_longer_a_mount():
    assert all(c.name != "webflow" for c in providers.MOUNT_CONNECTORS)


# --- surface MCP ------------------------------------------------------------

def test_webflow_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= set(all_tools)
    assert all(namespace_of(t) == "webflow"
               for t in all_tools if t.startswith("webflow_"))


def test_webflow_tools_all_have_descriptions(all_tools):
    # Régression du piège f-string-docstring (f"""..." n'alimente PAS
    # __doc__ -> FastMCP l'expose sans description).
    for name in EXPECTED_TOOLS:
        assert all_tools[name].description, f"{name} has no description"


# --- jointure tool <-> client oto-core (garde version-skew) -----------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.webflow.client import WebflowClient
    for meth in ("get_site", "list_collections", "get_collection", "list_items",
                 "get_item", "create_items", "update_items", "delete_items",
                 "publish_items"):
        assert callable(getattr(WebflowClient, meth, None)), \
            f"WebflowClient.{meth} manquant à l'oto-core épinglé"
