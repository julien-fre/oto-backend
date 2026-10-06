"""Supabase Management API — projects, auth config, logs.

Wraps `oto.tools.supabase.client` (module-level functions). The PAT (`sbp_…`)
is resolved per call via `access.resolve_api_key("supabase")` — byo, passed as
`token=` to each function (no process-level secret).
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /v1/projects` (already in the client — `list_projects`, called by
    `supabase_list_projects`). Bearer token (PAT `sbp_…`), read with no side
    effect. No particular cost or rate-limit mention for this call in the
    docs.

    **Authenticated ≠ usable** (oto#69 class): it does not distinguish here — an
    EMPTY list is a normal state (Supabase organization with no projects), not a
    refusal. Only the fact that the call did NOT raise matters.
    """
    from oto.tools.supabase import client as sb

    sb.list_projects(token=fields["key"])


def register(mcp: FastMCP) -> None:
    from oto.tools.supabase import client as sb

    connector_verify.register("supabase", _verify)

    def _token() -> str:
        key, _ = access.resolve_api_key("supabase")
        return key

    @mcp.tool()
    def supabase_list_projects() -> dict:
        """List the Supabase projects reachable with this access token."""
        return {"projects": sb.list_projects(token=_token())}

    @mcp.tool()
    def supabase_auth_config(project_ref: str) -> dict:
        """Auth config of a project (site_url, redirect allow-list, providers…).

        Args:
            project_ref: project ref (e.g. "doebdriroupduqpggcsj").
        """
        return sb.get_auth_config(project_ref, token=_token())

    @mcp.tool()
    def supabase_query_logs(
        project_ref: str,
        sql: Optional[str] = None,
        source: str = "auth_logs",
        limit: int = 50,
        minutes: int = 120,
    ) -> dict:
        """Query a project's logs (Logflare via the Management API).

        Args:
            sql: Logflare SQL. If omitted, returns the latest lines of `source`.
            source: auth_logs, edge_logs, function_edge_logs, function_logs,
                postgres_logs, postgrest_logs, storage_logs…
            minutes: time window (the API requires an iso timestamp range).
        """
        return {"rows": sb.query_logs(
            project_ref, sql=sql, source=source, limit=limit,
            minutes=minutes, token=_token())}
