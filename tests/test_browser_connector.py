"""Connecteur `browser` générique (oto-private#79) — logique pure.

Ce qu'on garde : la dérivation du site (l'identité de compte au coffre), l'isolation
par site (jamais le Context d'un autre site), et les gardes du flux de connexion
générique (`account_aware`, `force`). Le chemin Browserbase/DB est vérifié au deploy.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import browser_session, providers, tool_visibility
from oto_mcp.tools import browser


def test_site_is_the_normalized_host():
    # `www.` retiré : deux formes d'un même site = un seul login à maintenir.
    assert browser._site_of("https://www.le-ticket.fr/a/b?x=1") == "le-ticket.fr"
    assert browser._site_of("https://le-ticket.fr/") == "le-ticket.fr"
    # casse + port normalisés
    assert browser._site_of("https://Intranet.ACME.io:8443/x") == "intranet.acme.io"


@pytest.mark.parametrize("bad", ["", "le-ticket.fr", "file:///etc/passwd", "ftp://x.fr/"])
def test_non_absolute_http_url_is_refused(bad):
    with pytest.raises(McpError):
        browser._site_of(bad)


def test_registered_as_account_aware_session_connector():
    assert browser_session.is_session_connector("browser")
    assert "browser" in browser_session._ACCOUNT_AWARE


def test_registry_declares_a_multi_account_browser_connector():
    c = next(c for c in providers._REGISTRY_LIST if c.name == "browser")
    # un compte du coffre = un site → sessions isolées, pas un profil fourre-tout
    assert c.auth_multi_account
    assert c.namespaces == ("browser",)
    assert c.auth_modes == frozenset({"byo_user"})  # session loguée = personnelle


def test_eval_is_hidden_by_default_but_fetch_is_not():
    assert "browser_eval" in tool_visibility.DEFAULT_HIDDEN_TOOLS
    assert "browser_fetch" not in tool_visibility.DEFAULT_HIDDEN_TOOLS


def test_missing_site_credential_points_to_connect(monkeypatch):
    def _raise(*a, **k):
        raise McpError.__new__(McpError)  # forme indifférente : c'est le message qui compte
    monkeypatch.setattr(browser.access, "resolve_credential", _raise)
    with pytest.raises(McpError) as e:
        browser._context_id("le-ticket.fr")
    assert "browser_connect_start" in str(e.value)


def test_force_is_refused_for_single_site_connectors(monkeypatch):
    # `force` contourne la vérification : recevable seulement là où la vérification est
    # générique (connecteur account-aware). Sur pennylaneged & co le verify est une vraie
    # sonde d'API — la contourner n'aurait aucune justification.
    async def _v(_sid):
        return True
    browser_session.register("_test_single_site", _v)
    with pytest.raises(browser_session.SessionError):
        asyncio.run(browser_session.finalize(
            "sub-1", "_test_single_site", "ctx", "sess", force=True))


def test_account_aware_verify_receives_the_site(monkeypatch):
    seen: dict = {}

    async def _v(session_id, account):
        seen["args"] = (session_id, account)
        return False  # pas logué → finalize renvoie False sans rien persister

    browser_session.register("_test_generic", _v, account_aware=True)
    browser_session._PENDING[("sub-1", "ctx", "sess")] = float("inf")
    out = asyncio.run(browser_session.finalize(
        "sub-1", "_test_generic", "ctx", "sess", account="exemple.fr"))
    assert out is False
    assert seen["args"] == ("sess", "exemple.fr")
