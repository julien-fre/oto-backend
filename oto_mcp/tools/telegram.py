"""Telegram — messaging hosted via Unipile (Telegram account connected by the user).

The Telegram account lives at Unipile, connected by the user via hosted-auth
(dashboard, `?channel=telegram`). Tool `telegram_chat(op=…)` derived from the shared
messaging factory (see `tools/unipile.register_messaging_tools`).
"""
from __future__ import annotations

from fastmcp import FastMCP

from .unipile import register_messaging_tools


def register(mcp: FastMCP) -> None:
    register_messaging_tools(mcp, "TELEGRAM")
