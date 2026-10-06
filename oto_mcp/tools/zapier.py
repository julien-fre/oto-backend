"""Zapier — automation via the AI Actions API (exposed actions + execution).

Wraps `oto.tools.zapier.ZapierClient`. Credential = plain API key (`x-api-key`
header), keyed → resolved per call via `access.resolve_api_key("zapier")`.
byo (user/org), no platform key: everyone sets their own key (the set of
exposed actions is attached to the key, created on actions.zapier.com).

Model: Zapier exposes a catalogue of **actions** to agents that the user has
explicitly authorized, executable in natural language — not a Zap-management
API. `zapier_list_actions` discovers the actions, `zapier_execute_action`
runs one.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /exposed/` (already in the client — `list_actions`), the connector's
    only call: Zapier exposes neither `/me` nor a balance. An EMPTY list (no
    action exposed) is a normal state, never a refusal. `raise_for_upstream`
    (typed).

    **Authenticated ≠ usable** (oto#69 class): does not distinguish scope —
    a Zapier AI Actions key carries exactly the set of actions the user
    attached to it, there is nothing finer to distinguish.
    """
    from oto.tools.zapier import ZapierClient

    ZapierClient(api_key=fields["key"]).list_actions()


def register(mcp: FastMCP) -> None:
    from oto.tools.zapier import ZapierClient

    connector_verify.register("zapier", _verify)

    def _client() -> ZapierClient:
        key, _ = access.resolve_api_key("zapier")
        return ZapierClient(api_key=key)

    @mcp.tool()
    def zapier_list_actions() -> dict:
        """List the actions exposed by this Zapier key (id, description, params).

        Each action carries an `id` (pass it to zapier_execute_action) and the
        list of its configurable fields."""
        return _client().list_actions()

    @mcp.tool()
    def zapier_execute_action(
        action_id: str,
        instructions: str,
        params: Optional[dict] = None,
        preview_only: bool = False,
    ) -> dict:
        """Execute an exposed Zapier action.

        Args:
            action_id: action id (see zapier_list_actions).
            instructions: natural-language directive — Zapier fills the fields
                left in "AI guess" mode from this text.
            params: explicit overrides for the action's fields (take precedence
                over what is inferred from instructions).
            preview_only: True = don't run, return what would be done.
        """
        return _client().execute_action(
            action_id, instructions, params=params, preview_only=preview_only)

    @mcp.tool()
    def zapier_execution_log(execution_log_id: str) -> dict:
        """Get the detail of one execution (execution_log_id from execute)."""
        return _client().execution_log(execution_log_id)
