"""n8n — workflow automation (workflows + executions).

Wraps `oto.tools.n8n.N8nClient`. 2-field credential (API key + instance base URL,
self-hosting/n8n Cloud requires its own URL) → generic multi-field model
(ADR 0011), resolved per call via `access.resolve_credential_fields("n8n")`.
byo_user (no platform quota: the credential IS the grant).
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access, egress
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ALONE.

    `GET /workflows` (already in the client — `list_workflows`), `limit=1` — the
    smallest format available, n8n exposing neither `/me` nor a balance.

    ⚠️ `N8nClient._request` raises a BARE `Exception` on an HTTP refusal (no typed
    `status_code`) — like ashby, but WITHOUT its wall: no trace of a
    per-resource scoped key model at n8n (an n8n API key is tied to the
    user account that created it, not restricted per endpoint). The refusal
    therefore falls into `unknown` (never `unauthorized`) for lack of a typed code — honest,
    not a surrender: reopenable if the client starts typing its errors.

    **Authenticated ≠ usable** (oto#69 class): does not distinguish scopes —
    an n8n key carries the account's entire perimeter.
    """
    from oto.tools.n8n import N8nClient

    egress.check_url(fields["base_url"], connector="n8n")
    N8nClient(
        api_key=fields["api_key"], base_url=fields["base_url"],
    ).list_workflows(limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.n8n import N8nClient

    connector_verify.register("n8n", _verify)

    def _client() -> N8nClient:
        creds = access.resolve_credential_fields("n8n")
        # n8n is self-hosted: an instance on the platform's internal
        # network is exactly the case the guard must refuse without a
        # declared exception (`oto_mcp/egress.py`).
        egress.check_url(creds.get("base_url") or "", connector="n8n")
        return N8nClient(api_key=creds.get("api_key"),
                         base_url=creds.get("base_url"))

    @mcp.tool()
    def n8n_list_workflows(
        limit: int = 50,
        active: Optional[bool] = None,
        tags: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """List workflows (paginated via nextCursor).

        Args:
            active: keep only active/inactive workflows.
            tags: comma-separated tag names to filter by.
            cursor: pagination cursor (nextCursor from previous page).
        """
        return _client().list_workflows(
            limit=limit, active=active, tags=tags, cursor=cursor)

    @mcp.tool()
    def n8n_get_workflow(workflow_id: str) -> dict:
        """Get one workflow (nodes, connections, settings)."""
        return _client().get_workflow(workflow_id)

    @mcp.tool()
    def n8n_activate_workflow(workflow_id: str) -> dict:
        """Activate a workflow (its triggers/cron start running)."""
        return _client().activate_workflow(workflow_id)

    @mcp.tool()
    def n8n_deactivate_workflow(workflow_id: str) -> dict:
        """Deactivate a workflow."""
        return _client().deactivate_workflow(workflow_id)

    @mcp.tool()
    def n8n_list_executions(
        limit: int = 50,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """List workflow executions (paginated).

        Args:
            workflow_id: filter by workflow.
            status: "success" | "error" | "waiting".
            cursor: pagination cursor.
        """
        return _client().list_executions(
            limit=limit, workflow_id=workflow_id, status=status, cursor=cursor)

    @mcp.tool()
    def n8n_get_execution(
        execution_id: int, include_data: bool = False,
    ) -> dict:
        """Get one execution. `include_data` includes per-node run data (large)."""
        return _client().get_execution(execution_id, include_data=include_data)

    @mcp.tool()
    def n8n_list_tags(limit: int = 50, cursor: Optional[str] = None) -> dict:
        """List workflow tags."""
        return _client().list_tags(limit=limit, cursor=cursor)
