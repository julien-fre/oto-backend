"""New mount `lemlistmcp` (official Lemlist MCP, kind="mount") — verrouille the
`_build_transport(..., header=...)` path introduced for it, and its coexistence
with the pre-existing native `lemlist` connector (kind="tools", read-only by
design). Julien confirmed: full retirement was reconsidered — `lemlistmcp` ships
as a separate, new connector instead, so no existing customer's `lemlist`
integration is touched by this change.

Lemlist's official MCP authenticates via a static `X-API-Key` header, not OAuth
nor `Authorization: Bearer` — `factory_keyed` (mount.py) previously had only a
Bearer-only branch, with zero live consumers (AI Ark, pilot #152, since
re-qualified to a classic kind="tools" connector, #160). This file locks in the
new `mount_auth_header` field on `Connector`, that `_build_transport` sends the
named header instead of Authorization when it's set, and — critically — that
existing OAuth mounts (atlassian, planity) stay byte-identical in behavior.
"""
import asyncio

import pytest

from oto_mcp import providers
from oto_mcp.tools import mount


def _connector(name):
    c = providers.REGISTRY.get(name)
    assert c is not None and c.kind == "mount", f"{name} doit être un mount déclaré"
    return c


# --- registre ---------------------------------------------------------------

def test_lemlistmcp_is_declared_keyed_mount_with_custom_header():
    c = _connector("lemlistmcp")
    assert c.mount_url == "https://app.lemlist.com/mcp"
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert c.mount_auth_header == "X-API-Key"
    assert c.org_shareable, "byo_org doit rendre lemlistmcp org-shareable (indépendant de kind)"


def test_native_lemlist_connector_is_untouched():
    """La coexistence ne doit RIEN changer au connecteur natif existant — aucune
    migration cliente, aucun risque de régression pour un user déjà sur `lemlist`."""
    c = providers.REGISTRY["lemlist"]
    assert c.kind == "tools"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert c.keyed and c.secret_kind == "api_key"
    assert c.mount_url is None
    # le module tools/lemlist.py existe toujours et se charge dans le régime kind="tools"
    from oto_mcp.tools import lemlist as lemlist_module
    assert hasattr(lemlist_module, "register")


def test_lemlistmcp_and_lemlist_are_distinct_namespaces():
    from oto_mcp.tool_visibility import namespace_of
    assert namespace_of("lemlistmcp_list_campaigns") == "lemlistmcp"
    assert namespace_of("lemlist_list_campaigns") == "lemlist"


# --- _build_transport : le vrai changement de comportement -------------------

def test_build_transport_sends_named_header_when_given():
    t = mount._build_transport("https://app.lemlist.com/mcp", "secret-key", "X-API-Key")
    assert t.headers.get("X-API-Key") == "secret-key"
    assert "Authorization" not in t.headers


def test_build_transport_still_defaults_to_bearer_when_no_header():
    t = mount._build_transport("https://mcp.mento.cc/mcp", "a-token", None)
    assert t.headers.get("Authorization") == "Bearer a-token"
    assert "X-API-Key" not in t.headers


def test_build_transport_url_embedded_token_ignores_header_param():
    # {token} in URL takes precedence regardless of a header being passed —
    # unchanged precedence from before this change.
    t = mount._build_transport("https://example.com/mcp?token={token}", "tok123", "X-API-Key")
    assert "tok123" in t.url
    assert not t.headers.get("X-API-Key")


# --- factory_keyed : le chemin bout-en-bout (mocké, pas de réseau) -----------

def test_factory_keyed_injects_named_header(monkeypatch):
    """factory_keyed doit résoudre la clé via resolve_api_key puis construire un
    transport avec le header nommé — pas Authorization."""
    monkeypatch.setattr(mount, "current_user_sub_from_token", lambda: "sub-123")
    monkeypatch.setattr(mount.access, "resolve_api_key", lambda name: ("secret-key", False))

    factory = mount._make_factory(_connector("lemlistmcp"))
    client = asyncio.run(factory())

    assert client.transport.headers.get("X-API-Key") == "secret-key"
    assert "Authorization" not in client.transport.headers


def test_factory_keyed_still_gates_without_sub():
    from mcp.shared.exceptions import McpError

    factory = mount._make_factory(_connector("lemlistmcp"))
    with pytest.raises(McpError):
        asyncio.run(factory())


# --- non-régression : les mounts OAuth existants sont inchangés --------------

def test_oauth_mount_factory_still_uses_bearer_not_broken_by_new_param():
    """Contraste : atlassian (OAuth, mount_auth_header=None par défaut) doit rester
    Bearer-only et gater exactement comme avant — non affecté par le nouveau
    paramètre `header` de _build_transport."""
    from mcp.shared.exceptions import McpError

    c = _connector("atlassian")
    assert c.mount_auth_header is None
    factory = mount._make_factory(c)
    with pytest.raises(McpError):
        asyncio.run(factory())
