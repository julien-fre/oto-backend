"""Lien journal → traceback : l'event Sentry d'un appel atterrit sur sa ligne tool_calls.

Le point RISQUÉ n'est pas la capture mais la PROPAGATION : `SentryToolErrorMiddleware`
(innermost) pose une ContextVar, `ToolCallLogger` (plus externe) écrit la ligne depuis
une tâche créée par `asyncio.create_task` — la valeur doit survivre à la copie de
contexte. On exerce donc la VRAIE chaîne (les deux middlewares réels, un tool qui
lève), jamais des stubs : c'est exactement le genre de contrat qu'un test stubbé
déclare vert à tort.
"""
import pytest
import sentry_sdk
from fastmcp import Client, FastMCP

from oto_mcp import sentry_setup
from oto_mcp.calllog import ToolCallLogger


def _server(monkeypatch, *, event_id, rows):
    """Chaîne réelle dans l'ordre de prod : ToolCallLogger puis Sentry (innermost)."""
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda e: event_id)

    mcp = FastMCP("t")

    @mcp.tool()
    def boom() -> str:
        raise RuntimeError("krach interne")

    @mcp.tool()
    def fine() -> str:
        return "ok"

    async def sink(row: dict) -> None:
        # Le handshake `initialize` émet aussi une ligne (kind='protocol') : hors sujet
        # ici, ce test porte sur les lignes d'APPEL D'OUTIL et leur ordre.
        if row.get("kind") == "protocol":
            return
        # Ce que fait `server._calllog_sink` : relire la ContextVar depuis la tâche
        # d'insertion, où le contexte a été COPIÉ au create_task.
        row["sentry_event_id"] = sentry_setup.current_tool_event_id()
        rows.append(row)

    mcp.add_middleware(ToolCallLogger(sink, server="test"))
    mcp.add_middleware(sentry_setup.SentryToolErrorMiddleware())
    return mcp


@pytest.mark.asyncio
async def test_event_id_reaches_the_call_log_row(monkeypatch):
    rows: list[dict] = []
    mcp = _server(monkeypatch, event_id="abc123", rows=rows)
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("boom", {})
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["sentry_event_id"] == "abc123"


@pytest.mark.asyncio
async def test_successful_call_carries_no_event_id(monkeypatch):
    """Pas de fuite : un appel sain ne récupère pas l'event d'un appel précédent."""
    rows: list[dict] = []
    mcp = _server(monkeypatch, event_id="abc123", rows=rows)
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("boom", {})
        await c.call_tool("fine", {})
    assert [r["sentry_event_id"] for r in rows] == ["abc123", None]


@pytest.mark.asyncio
async def test_managed_error_is_not_stamped(monkeypatch):
    """Une erreur GÉRÉE (4xx amont) n'est pas capturée → pas d'event sur la ligne."""
    rows: list[dict] = []
    mcp = _server(monkeypatch, event_id="abc123", rows=rows)
    monkeypatch.setattr(sentry_setup, "_is_expected_error", lambda e: True)
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("boom", {})
    assert rows[0]["sentry_event_id"] is None
