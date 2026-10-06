"""Teamtailor ATS — candidates, jobs, applications (JSON:API).

Wraps `oto.tools.teamtailor.TeamtailorClient` (API key in the
`Authorization: Token token=…` header). Key resolved per call via
`access.resolve_api_key("teamtailor")` — byo (user key on /account or the org's
shared credential). No platform key.

⚠️ Responses are in **JSON:API** format: resources under `data` with `{type, id,
attributes, relationships}`. Pagination by `page_number`/`page_size`.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /jobs` (already in the client — `list_jobs`), `page_size=1` — the
    smallest format available, since Teamtailor exposes neither `/me` nor a
    balance. It is also a REAL read of the connector (`teamtailor_jobs`), not an
    endpoint invented for the probe: if a valid key can't read jobs, that
    tool is already broken for it.

    **Authenticated ≠ usable** (oto#69 class): doesn't distinguish scope —
    no trace, in the docs or the client, of a Teamtailor key restricted
    per resource (unlike Ashby, see the decision not to probe ashby,
    otomata-tech/oto#69).
    """
    from oto.tools.teamtailor.client import TeamtailorClient

    TeamtailorClient(api_key=fields["key"]).list_jobs(page_size=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.teamtailor.client import TeamtailorClient

    connector_verify.register("teamtailor", _verify)

    def _client() -> TeamtailorClient:
        key, _ = access.resolve_api_key("teamtailor")
        return TeamtailorClient(api_key=key)

    @mcp.tool()
    def teamtailor_candidates(
        page_size: int = 30, page_number: int = 1, email: Optional[str] = None,
    ) -> dict:
        """List candidates (paginated, JSON:API). `email` filters by exact email."""
        return _client().list_candidates(
            page_size=page_size, page_number=page_number, email=email)

    @mcp.tool()
    def teamtailor_candidate(candidate_id: str) -> dict:
        """Fetch one candidate by id."""
        return _client().get_candidate(candidate_id)

    @mcp.tool()
    def teamtailor_create_candidate(attributes: dict) -> dict:
        """Create a candidate.

        Args:
            attributes: JSON:API attributes (first-name, last-name, email, phone,
                pitch, tags, …). Wrapped in {data:{type, attributes}} by the client.
        """
        return _client().create_candidate(attributes)

    @mcp.tool()
    def teamtailor_jobs(
        page_size: int = 30, page_number: int = 1, status: Optional[str] = None,
    ) -> dict:
        """List jobs. status: open | draft | archived | unlisted."""
        return _client().list_jobs(
            page_size=page_size, page_number=page_number, status=status)

    @mcp.tool()
    def teamtailor_job(job_id: str) -> dict:
        """Fetch one job by id."""
        return _client().get_job(job_id)

    @mcp.tool()
    def teamtailor_job_applications(
        page_size: int = 30, page_number: int = 1, job_id: Optional[str] = None,
    ) -> dict:
        """List job applications, optionally filtered by job_id."""
        return _client().list_job_applications(
            page_size=page_size, page_number=page_number, job_id=job_id)
