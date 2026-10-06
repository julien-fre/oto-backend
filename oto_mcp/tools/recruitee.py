"""Recruitee ATS — candidates, offers (jobs), notes.

Wrappe `oto.tools.recruitee.RecruiteeClient`. 2-field credential (API token +
company id) → generic multi-field model (ADR 0011), resolved per call via
`access.resolve_credential_fields("recruitee")`. byo_user (no platform quota:
the credential IS the grant).

Vocabulary: a job = an **offer**; a candidate is attached to one or more offers.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /c/<company_id>/candidates` (already in the client — `list_candidates`),
    `limit=1` — the smallest available shape, since Recruitee exposes neither `/me`
    nor a balance. Bearer token + `company_id` (the credential's two fields),
    read-only with no side effects.

    **Authenticated ≠ usable** (class oto#69): does not distinguish scope —
    a personal Recruitee token carries the full scope of the account that created it.
    """
    from oto.tools.recruitee.client import RecruiteeClient

    RecruiteeClient(
        api_token=fields["api_token"], company_id=fields["company_id"],
    ).list_candidates(limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.recruitee.client import RecruiteeClient

    connector_verify.register("recruitee", _verify)

    def _client() -> RecruiteeClient:
        creds = access.resolve_credential_fields("recruitee")
        return RecruiteeClient(
            api_token=creds.get("api_token"),
            company_id=creds.get("company_id"),
        )

    @mcp.tool()
    def recruitee_candidates(
        limit: int = 50, offset: int = 0,
        offer_id: Optional[int] = None, query: Optional[str] = None,
    ) -> dict:
        """List candidates (paginated).

        Args:
            offer_id: filter by job (offer).
            query: search by name/email.
        """
        return _client().list_candidates(
            limit=limit, offset=offset, offer_id=offer_id, query=query)

    @mcp.tool()
    def recruitee_candidate(candidate_id: int) -> dict:
        """Fetch one candidate by id."""
        return _client().get_candidate(candidate_id)

    @mcp.tool()
    def recruitee_create_candidate(
        candidate: dict, offer_ids: Optional[list[int]] = None,
    ) -> dict:
        """Create a candidate.

        Args:
            candidate: candidate object (name, emails, phones, social_links,
                links, cover_letter, …).
            offer_ids: jobs (offers) to attach the candidate to.
        """
        return _client().create_candidate(candidate, offer_ids=offer_ids)

    @mcp.tool()
    def recruitee_add_note(candidate_id: int, body: str) -> dict:
        """Add a note to a candidate."""
        return _client().add_note(candidate_id, body)

    @mcp.tool()
    def recruitee_offers(
        scope: Optional[str] = None, kind: Optional[str] = None,
    ) -> dict:
        """List offers (jobs). scope: active | archived | not_archived ;
        kind: job | talent_pool."""
        return _client().list_offers(scope=scope, kind=kind)

    @mcp.tool()
    def recruitee_offer(offer_id: int) -> dict:
        """Fetch one offer (job) by id."""
        return _client().get_offer(offer_id)
