"""Provider `resend` — credential-only (the org's Resend key), no tool of its own.

The key is resolved by `email_send` (transport=resend) via
`access.resolve_api_key("resend")` (user > org cascade). This module exists
only to satisfy the invariant "one tools/ file per provider
kind=tools" (test_capabilities_drift); `register_all` imports it and calls
`register()` which registers nothing.
"""
from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:  # noqa: ARG001 — credential consumed by email_send
    return
