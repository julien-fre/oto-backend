"""Topograph — KYB data & documents for European public registries.

Wraps `oto.tools.topograph.TopographClient` (public API https://docs.topograph.co).
Key resolved per call via `access.resolve_api_key("topograph")` — byo-only provider
(user key set on the dashboard, or the active org's shared credential). No
platform key (Topograph = pay-per-request, everyone connects their own account).
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access


def register(mcp: FastMCP) -> None:
    from oto.tools.topograph.client import TopographClient

    def _client() -> TopographClient:
        key, _ = access.resolve_api_key("topograph")
        return TopographClient(api_key=key)

    @mcp.tool()
    def topograph_search(
        query: str,
        country: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """Search European companies by name or registration number (Topograph KYB).

        Returns candidate companies with their identity + registration number, to
        then fetch full data with `topograph_company`.

        Args:
            query: company name or registration number.
            country: ISO 3166-1 alpha-2 country code (e.g. "FR", "GB", "DE").
            limit: max number of results.
        """
        return _client().search(query=query, country=country, limit=limit)

    @mcp.tool()
    def topograph_company(
        country: Optional[str] = None,
        registration_number: Optional[str] = None,
        company_id: Optional[str] = None,
        mode: str = "onboarding",
    ) -> dict:
        """Get normalized company data from European registers (Topograph KYB).

        Identify the company by (`country` + `registration_number`) — both returned
        by `topograph_search` — or by `company_id`. `mode="onboarding"` is fast and
        cost-effective; `mode="verification"` is the rigorous KYB check.

        Args:
            country: ISO 3166-1 alpha-2 country code.
            registration_number: registration number (SIREN/SIRET, Companies House…).
            company_id: Topograph company id (alternative to registration_number).
            mode: "onboarding" or "verification".
        """
        return _client().company(
            country=country,
            registration_number=registration_number,
            company_id=company_id,
            mode=mode,
        )
