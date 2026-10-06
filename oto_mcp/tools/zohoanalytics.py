"""Zoho Analytics — data reading (workspaces, views, export, SQL queries).

Credential = OAuth2 (self-client) with 5 fields: client_id + client_secret +
refresh_token + org_id (`ZANALYTICS-ORGID` header) + data_center → generic
multi-field model (ADR 0011), resolved per call via
`access.resolve_credential_fields("zohoanalytics")`. byo_user OR byo_org (key
shareable by the data team). Access token derived/cached in memory client-side.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, status_hints
from ..connectors import verify as connector_verify


# Zoho hosts per regional data center; the self-client AND the refresh token are
# tied to their region of issue (a `.eu` self-client hitting `accounts.zoho.com`
# is rejected with an opaque `invalid_client`). The `data_center` field selects
# the Analytics API + OAuth domains. Recognized regions:
_DC_DOMAINS = {
    "com": ("https://analyticsapi.zoho.com", "https://accounts.zoho.com"),
    "eu": ("https://analyticsapi.zoho.eu", "https://accounts.zoho.eu"),
    "in": ("https://analyticsapi.zoho.in", "https://accounts.zoho.in"),
    "au": ("https://analyticsapi.zoho.com.au", "https://accounts.zoho.com.au"),
    "jp": ("https://analyticsapi.zoho.jp", "https://accounts.zoho.jp"),
    "ca": ("https://analyticsapi.zohocloud.ca", "https://accounts.zohocloud.ca"),
    "sa": ("https://analyticsapi.zoho.sa", "https://accounts.zoho.sa"),
}


def _resolve_dc_domains(data_center: Optional[str]) -> tuple[str, str]:
    """`(api_domain, accounts_url)` for the declared Zoho region. Missing
    or unrecognized region → actionable `McpError`, **never** a silent fallback to
    `com` (which would mask the real cause of an `invalid_client`)."""
    dc = (data_center or "").strip().lower()
    if dc not in _DC_DOMAINS:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=(
            (f"Zoho data center not recognized: {data_center!r}." if dc
             else "Zoho data center missing.")
            + " Fill in your region in the \"Data center\" field of the Zoho"
            " Analytics connector — one of: com, eu, in, au, jp, ca, sa. It is visible in"
            " the URL when you are logged in to Zoho Analytics (e.g. analytics.zoho.eu → \"eu\")."
        )))
    return _DC_DOMAINS[dc]


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /restapi/v2/orgs` (already in the client — `list_orgs`), the smallest
    call available: there is no separate `/me` at Zoho Analytics, `list_orgs`
    lists the Zoho organizations reachable by this self-client. The OAuth refresh
    (`ZohoAnalyticsClient._get_access_token`) already validates client_id +
    client_secret + refresh_token + data_center in one go; the data call
    raises via `raise_for_upstream` (typed, `UpstreamHTTPError`).

    **Authenticated ≠ usable** (class oto#69): does not distinguish scope —
    a Zoho Analytics self-client has a single scope (Analytics), no granular
    per-workspace permission at the probe level.
    """
    from oto.tools.zohoanalytics.client import ZohoAnalyticsClient

    status_hints.require_complete("zohoanalytics", fields)
    api_domain, accounts_url = _resolve_dc_domains(fields.get("data_center"))
    ZohoAnalyticsClient(
        client_id=fields.get("client_id"), client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"), org_id=fields.get("org_id"),
        api_domain=api_domain, accounts_url=accounts_url,
    ).list_orgs()


def register(mcp: FastMCP) -> None:
    from oto.tools.zohoanalytics.client import ZohoAnalyticsClient

    connector_verify.register("zohoanalytics", _verify)

    def _client() -> ZohoAnalyticsClient:
        creds = access.resolve_credential_fields("zohoanalytics")
        api_domain, accounts_url = _resolve_dc_domains(creds.get("data_center"))
        return ZohoAnalyticsClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            org_id=creds.get("org_id"),
            api_domain=api_domain,
            accounts_url=accounts_url,
        )

    @mcp.tool()
    def zohoanalytics_workspaces() -> dict:
        """List all Zoho Analytics workspaces accessible to the user (owned + shared)."""
        return _client().list_workspaces()

    @mcp.tool()
    def zohoanalytics_views(
        workspace_id: str, view_types: Optional[list[int]] = None,
    ) -> dict:
        """List the views (tables, charts, reports…) of a workspace.

        Args:
            view_types: optional filter by Zoho view-type code —
                0 Table, 2 Chart, 3 Pivot, 4 Summary, 6 QueryTable, 7 Dashboard.
        """
        return _client().list_views(workspace_id, view_types=view_types)

    @mcp.tool()
    def zohoanalytics_view_details(view_id: str) -> dict:
        """Get the metadata of one view — columns (name + type), view type, folder.

        `view_id` is the globally-unique id from `zohoanalytics_views` (no
        workspace needed). Use this to discover a view's columns before querying
        it with `zohoanalytics_query`.
        """
        return _client().get_view_details(view_id)

    @mcp.tool()
    def zohoanalytics_export(
        workspace_id: str,
        view_id: str,
        response_format: str = "json",
        criteria: Optional[str] = None,
        selected_columns: Optional[list[str]] = None,
    ) -> dict:
        """Export the data of a view (synchronous).

        Returns the parsed JSON for json, else {"data": <raw text>}.

        Args:
            response_format: json (default) | csv | xml | xls | pdf | html | image.
            criteria: Zoho row filter, e.g. '"Sales" > 500'.
            selected_columns: restrict to these column names.
        """
        out = _client().export_view(
            workspace_id, view_id, response_format=response_format,
            criteria=criteria, selected_columns=selected_columns)
        return out if isinstance(out, dict) else {"data": out}

    @mcp.tool()
    def zohoanalytics_query(
        workspace_id: str, sql_query: str, response_format: str = "json",
    ) -> dict:
        """Run a SQL SELECT query over a workspace's tables (async bulk export,
        resolved server-side: create job → poll → download).

        Returns the parsed JSON for json, else {"data": <raw text>}.

        Args:
            sql_query: a SELECT statement over the workspace tables, e.g.
                'select Region, sum("Sales") from "Sales" group by Region'.
            response_format: json (default) | csv.
        """
        out = _client().query_sql(
            workspace_id, sql_query, response_format=response_format)
        return out if isinstance(out, dict) else {"data": out}
