"""Make (ex-Integromat) — workflow automation (scenarios + executions).

Wraps `oto.tools.make.MakeClient`. 2-field credential (API token + zone base URL,
Make is regionalized: eu1/us1/eu2…) → generic multi-field model
(ADR 0011), resolved per call via `access.resolve_credential_fields("make")`.
byo_user (no platform quota: the credential IS the grant).

Make vocabulary: a workflow = a **scenario**; it belongs to a **team**,
itself within an **organization**. Listing scenarios requires a team_id.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access, egress
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /organizations` (already in the client — `list_organizations`), the
    connector's first discovery call, not an endpoint invented for the
    probe: Make exposes neither `/me` nor a balance. `raise_for_upstream` (typed).

    **Authenticated ≠ usable** (oto#69 class): does not distinguish scope —
    a Make token carries the account's entire scope for its zone.
    """
    from oto.tools.make import MakeClient

    egress.check_url(fields["base_url"], connector="make")
    MakeClient(
        api_token=fields["api_token"], base_url=fields["base_url"],
    ).list_organizations()


def register(mcp: FastMCP) -> None:
    from oto.tools.make import MakeClient

    connector_verify.register("make", _verify)

    def _client() -> MakeClient:
        creds = access.resolve_credential_fields("make")
        egress.check_url(creds.get("base_url") or "", connector="make")
        return MakeClient(api_token=creds.get("api_token"),
                          base_url=creds.get("base_url"))

    @mcp.tool()
    def make_list_organizations() -> dict:
        """List organizations reachable with this token (to discover ids)."""
        return _client().list_organizations()

    @mcp.tool()
    def make_list_teams(organization_id: int) -> dict:
        """List teams of an organization (teams own the scenarios)."""
        return _client().list_teams(organization_id)

    @mcp.tool()
    def make_list_scenarios(
        team_id: int, limit: int = 50, offset: int = 0,
    ) -> dict:
        """List a team's scenarios (paginated).

        Args:
            team_id: team id (see make_list_teams).
        """
        return _client().list_scenarios(team_id, limit=limit, offset=offset)

    @mcp.tool()
    def make_get_scenario(scenario_id: int) -> dict:
        """Get a scenario (metadata, scheduling, state)."""
        return _client().get_scenario(scenario_id)

    @mcp.tool()
    def make_get_scenario_blueprint(scenario_id: int) -> dict:
        """Get a scenario's blueprint (its modules structure)."""
        return _client().get_scenario_blueprint(scenario_id)

    @mcp.tool()
    def make_run_scenario(
        scenario_id: int,
        data: Optional[dict] = None,
        responsive: bool = True,
    ) -> dict:
        """Trigger a scenario run.

        Args:
            data: input payload passed to the scenario (depends on its modules).
            responsive: wait for the run to finish (True) or return immediately.
        """
        return _client().run_scenario(scenario_id, data=data, responsive=responsive)

    @mcp.tool()
    def make_list_scenario_logs(
        scenario_id: int, limit: int = 50, offset: int = 0,
    ) -> dict:
        """List a scenario's execution logs (paginated)."""
        return _client().list_scenario_logs(scenario_id, limit=limit, offset=offset)
