"""Provider `routine` — credential-only (Claude Code routine), no tool of its own.

The credential (`routine_id` + trigger token) is resolved by the capability
`me.automation.fire` (`capabilities/automation.py`) via
`access.resolve_credential_fields("routine")`. The verb lives in a CAPABILITY and not here,
because the dashboard will want a "trigger" button: an `@mcp.tool()` would have
forced a second REST implementation (ADR 0042 §Surface convergence).

This module exists only to satisfy the invariant "one tools/ file per
kind=tools provider" (test_capabilities_drift); `register_all` imports it and calls
`register()`, which registers nothing.
"""
from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:  # noqa: ARG001 — credential consumed by the capability
    return
