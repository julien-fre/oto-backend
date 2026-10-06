"""Phantombuster — automation agents (launch + monitor + results).

Wraps `oto.tools.phantombuster.PhantombusterClient`. Key resolved per call via
`access.resolve_api_key("phantombuster")` — byo.

Note: `phantombuster_launch_agent` triggers a run (may consume Phantombuster
credits and act on third-party accounts). The other tools are read-only.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /containers` (already in the client — `list_containers`), without
    `agent_id`: lists the recent runs of the ENTIRE ACCOUNT, `limit=1` —
    the smallest format available, Phantombuster exposing neither `/me` nor a
    balance on this call. An EMPTY list (no runs) is a normal state.

    **Authenticated ≠ usable** (class oto#69): does not distinguish scope —
    a Phantombuster key carries the account's entire scope.
    """
    from oto.tools.phantombuster.client import PhantombusterClient

    PhantombusterClient(api_key=fields["key"]).list_containers(limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.phantombuster.client import PhantombusterClient

    connector_verify.register("phantombuster", _verify)

    def _client() -> PhantombusterClient:
        key, _ = access.resolve_api_key("phantombuster")
        return PhantombusterClient(api_key=key)

    @mcp.tool()
    def phantombuster_get_agent(agent_id: str) -> dict:
        """Get an agent's configuration and status."""
        return _client().get_agent(agent_id)

    @mcp.tool()
    def phantombuster_list_containers(
        agent_id: Optional[str] = None, limit: int = 10,
    ) -> dict:
        """List recent containers (runs), optionally filtered to one agent."""
        return {"containers": _client().list_containers(agent_id=agent_id, limit=limit)}

    @mcp.tool()
    def phantombuster_get_container(container_id: str) -> dict:
        """Get a container (run) status and metadata."""
        return _client().get_container(container_id)

    @mcp.tool()
    def phantombuster_container_results(container_id: str) -> dict:
        """Get the parsed JSON results produced by a finished container."""
        return {"results": _client().get_container_results(container_id)}

    @mcp.tool()
    def phantombuster_container_output(container_id: str) -> dict:
        """Get a container's output logs (text)."""
        return {"output": _client().get_container_output(container_id)}

    @mcp.tool()
    def phantombuster_launch_agent(
        agent_id: str, config: Optional[dict] = None,
    ) -> dict:
        """Launch an agent (starts a run). Returns the new containerId.

        Args:
            config: optional overrides (argument, bonusArgument…) merged into the
                launch payload.
        """
        return _client().launch_agent(agent_id, config=config)
