"""oto-backend#867 lot 2 — la résolution des pièces jointes de `gmail_compose` ne
bloque plus la boucle d'événements, et une source lente rend une erreur nommée.

`file_source.resolve` (drive/gmail/url) fait un appel HTTP synchrone par pièce
jointe. Les appels à l'API Gmail elle-même (`client.create_draft`/`send`/…) sont
déjà en `asyncio.to_thread` ; seule la résolution des pièces jointes, en amont,
tournait encore nûment dans la boucle.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _joue(coro):
    porteur: dict = {}

    async def _run():
        porteur["boucle"] = threading.current_thread()
        return await coro
    try:
        result = asyncio.run(_run())
        return porteur["boucle"], result, None
    except McpError as e:
        return porteur["boucle"], None, e


@pytest.fixture
def wired(monkeypatch):
    from oto_mcp.tools import gmail as G
    monkeypatch.setattr(G, "_client_for_user", lambda account=None: MagicMock())
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    return G


def test_attachments_tournent_hors_boucle(monkeypatch, wired):
    vu: dict = {}

    def _resolve(src):
        vu["thread"] = threading.current_thread()
        from oto_mcp.file_source import ResolvedFile
        return ResolvedFile(b"x", "a.pdf", "application/pdf")

    monkeypatch.setattr(wired.file_source, "resolve", _resolve)
    fn = _tool("gmail_compose")
    boucle, result, err = _joue(fn(
        body="hi", to="a@b.com",
        attachments=[{"kind": "url", "url": "https://example.invalid/x"}]))
    assert err is None, err
    assert vu["thread"] is not boucle, (
        "file_source.resolve a tourné dans le thread de l'event loop lors de la "
        "composition d'un email — une source lente gèlerait tout le processus "
        "(oto-backend#867)")


def test_attachments_lentes_rendent_une_erreur_nommee(monkeypatch, wired):
    monkeypatch.setattr(wired, "_ATTACHMENTS_TIMEOUT_S", 0.05)

    def _lent(src):
        import time
        time.sleep(1)

    monkeypatch.setattr(wired.file_source, "resolve", _lent)
    fn = _tool("gmail_compose")
    _, _, err = _joue(fn(
        body="hi", to="a@b.com",
        attachments=[{"kind": "url", "url": "https://example.invalid/x"}]))
    assert err is not None and "trop longue" in err.error.message, (
        f"une pièce jointe lente doit rendre une McpError nommée, pas un gel — reçu {err!r}")


def test_le_controle_mord__un_appel_NU_dans_la_boucle_est_detecte():
    """Contrôle négatif, comme aux lots précédents."""
    vu: dict = {}

    async def _nu():
        vu["thread"] = threading.current_thread()
        return 1

    boucle, _, _ = _joue(_nu())
    assert vu["thread"] is boucle, "la sonde elle-même doit savoir dire « dans la boucle »"
