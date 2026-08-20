"""Connecteur Webflow — verrouille : l'entrée registre (keyed, un seul champ —
un Site API token Webflow est bound à UN site, le client oto-core résout
`site_id` lui-même via `GET /sites`, rien à saisir côté credential), la
surface MCP (5 tools — CMS consolidé en UN SEUL tool `webflow_cms`, publish
et webhooks séparés, forms/submissions séparés), et la jointure tool <->
client oto-core (garde version-skew).
"""
import asyncio

import pytest

from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "webflow_cms",
    "webflow_publish",
    "webflow_webhooks",
    "webflow_forms",
    "webflow_submissions",
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

def test_webflow_is_classic_keyed_connector():
    # Un Site API token Webflow est bound à UN site (Site tokens are created
    # per site) : rien à saisir en plus du token, le client oto-core résout
    # site_id lui-même via GET /sites -> keyed=True, un seul champ, comme
    # folk/cognism (paste-the-token).
    c = providers.REGISTRY["webflow"]
    assert c.kind == "tools"
    assert c.mount_url is None
    assert c.keyed is True
    assert c.secret_kind == "api_key"
    assert "webflow" in providers.KEY_PROVIDERS


def test_webflow_is_byo_only_no_platform_mode():
    c = providers.REGISTRY["webflow"]
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "platform" not in c.auth_modes


def test_webflow_credential_fields_shape():
    c = providers.REGISTRY["webflow"]
    fields = {f.name: f for f in c.credential_fields}
    assert set(fields) == {"token"}
    assert fields["token"].secret is True


def test_webflow_deny_by_default():
    c = providers.REGISTRY["webflow"]
    assert c.default_active is False


def test_webflow_no_longer_a_mount():
    assert all(c.name != "webflow" for c in providers.MOUNT_CONNECTORS)


# --- surface MCP : CMS = UN SEUL tool visible ------------------------------

def test_webflow_surface_is_exactly_five_tools(all_tools):
    """Le CMS (site/collections/items) est consolidé dans UN tool
    (`webflow_cms`, verbe en `op`) — ni `webflow_site`, ni
    `webflow_collections`, ni `webflow_items` séparés ne doivent réapparaître :
    côté agent comme côté carte connecteur du dashboard (qui liste les outils
    d'un connecteur sous UNE carte), le CMS doit se présenter comme une seule
    chose. `webflow_publish` (la seule action qui rend du contenu public),
    `webflow_webhooks` (domaine distinct) et `webflow_forms`/
    `webflow_submissions` (catalogue de formulaires vs données de contact
    réelles — formes de paramètres disjointes) restent séparés."""
    names = {t for t in all_tools if t.startswith("webflow_")}
    assert names == EXPECTED_TOOLS


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
                 "publish_items", "list_webhooks", "get_webhook",
                 "create_webhook", "delete_webhook", "list_forms", "get_form",
                 "list_form_submissions", "get_form_submission",
                 "update_form_submission", "delete_form_submission"):
        assert callable(getattr(WebflowClient, meth, None)), \
            f"WebflowClient.{meth} manquant à l'oto-core épinglé"
