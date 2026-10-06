"""Provider `scaleway` — Otomata-hosted email (Scaleway TEM), credential/config-only.

No tool of its own: sending is performed by `email_send` (spine) via the
`otomata-auth-mailer` service ("mailer" transport), no org key. Management (senders
+ quiet window) lives in `orgs.email_settings` keyed by connector, surfaced by the
email panel of the ORG connector card. This module exists to satisfy the invariant
"one tools/ file per kind=tools provider" (test_capabilities_drift); `register()`
registers nothing.
"""
from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:  # noqa: ARG001 — config-only, sending via email_send
    return
