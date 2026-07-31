"""Apollo.io — B2B prospection (organizations, people, job postings).

Wrappe `oto.tools.apollo.ApolloClient`. Clé résolue par appel via
`access.resolve_api_key("apollo")` — user key (`/account`) prioritaire, sinon
clé plateforme (free-tier, quota daily = `default_quota` par user/jour).

Le quota plateforme métré = les **crédits Apollo** (l'appel `people/match` qui
révèle un contact). Recherche org/people et job postings ne consomment pas de
crédit Apollo → non métrés (ils restent servis par la clé plateforme).
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access


def register(mcp: FastMCP) -> None:
    from oto.tools.apollo.client import ApolloClient

    def _client() -> tuple[ApolloClient, bool]:
        key, is_platform = access.resolve_api_key("apollo")
        return ApolloClient(api_key=key), is_platform

    @mcp.tool()
    def apollo_search_organizations(
        name: Optional[str] = None,
        domain: Optional[str] = None,
        country: Optional[str] = None,
        employee_ranges: Optional[list[str]] = None,
        revenue_min: Optional[int] = None,
        revenue_max: Optional[int] = None,
        locations: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        technologies: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        per_page: int = 10,
        page: int = 1,
    ) -> dict:
        """Find companies by firmographics — the CHEAP way to qualify a list.

        Costs 1 Apollo credit per PAGE (up to 100 results), where enrichment costs
        1 credit per COMPANY: filter here, enrich only what you keep.

        ⚠️ Results carry revenue and headcount GROWTH, but NOT the headcount itself
        — that's why you filter by `employee_ranges` instead of reading a number.
        For the exact headcount and its per-department split, enrich (see
        apollo_enrich_organization / apollo_bulk_enrich_organizations).

        Args:
            name: company name.
            domain: company domain.
            country: HQ country (shorthand for `locations`).
            employee_ranges: headcount brackets "min,max", e.g. ["11,50", "51,200"].
            revenue_min / revenue_max: annual revenue bounds.
            locations: HQ cities/regions/countries.
            keywords: activity keywords.
            technologies: technology uids in use, e.g. ["salesforce"].
            org_ids: Apollo organization ids.
            per_page: results per page (≤100). page: page number.
        """
        client, _ = _client()
        return client.search_organizations(
            name=name, domain=domain, country=country, per_page=per_page, page=page,
            employee_ranges=employee_ranges, revenue_min=revenue_min,
            revenue_max=revenue_max, locations=locations, keywords=keywords,
            technologies=technologies, org_ids=org_ids)

    @mcp.tool()
    def apollo_enrich_organization(domain: str) -> dict:
        """Enrich a company from its domain (firmographics, size, industry…).

        Returns the exact `estimated_num_employees`, its per-department split
        (`departmental_head_count`), 6/12/24-month headcount growth, revenue,
        founding year and tech stack. Costs 1 Apollo credit. For several companies
        at once, prefer apollo_bulk_enrich_organizations (same cost, 10× fewer calls).
        """
        client, _ = _client()
        return client.enrich_organization(domain)

    @mcp.tool()
    def apollo_bulk_enrich_organizations(domains: list[str]) -> dict:
        """Enrich UP TO 10 companies in a single call — same fields as
        apollo_enrich_organization (headcount, per-department split, growth, revenue).

        Costs 1 Apollo credit per company (a batch saves CALLS, not credits: the
        enrich rate limit is 600/h, so batching divides your call budget by 10).
        Over 10 domains, split into batches yourself — the API refuses more.
        """
        client, _ = _client()
        return client.bulk_enrich_organizations(domains)

    @mcp.tool()
    def apollo_search_people(
        domains: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        titles: Optional[list[str]] = None,
        seniorities: Optional[list[str]] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> dict:
        """Search people by company domains/ids, titles, seniorities (net-new).

        Returns identities WITHOUT email/phone — reveal a contact with
        apollo_match_person (which costs an Apollo credit).

        Args:
            domains: company domains, e.g. ["acme.com"].
            org_ids: Apollo organization ids (from apollo_enrich_organization).
            titles: job-title keywords, e.g. ["directeur financier", "CFO"].
            seniorities: e.g. ["c_suite", "founder", "owner", "director", "manager"].
        """
        client, _ = _client()
        return client.search_people(
            domains=domains, org_ids=org_ids,
            titles=titles, seniorities=seniorities, per_page=per_page, page=page)

    @mcp.tool()
    def apollo_match_person(
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> dict:
        """Match a single person (enrichment). Returns {} if no match.

        Pass the strongest identifier you have (linkedin_url or email best).
        Coût : 1 crédit Apollo par appel (compté sur le quota plateforme).
        """
        client, is_platform = _client()
        result = client.match_person(
            linkedin_url=linkedin_url, email=email, first_name=first_name,
            last_name=last_name, name=name, domain=domain, org_name=org_name) or {}
        if is_platform:
            access.record_platform_usage("apollo")
        return result

    @mcp.tool()
    def apollo_job_postings(org_id: str) -> dict:
        """List active job postings for an Apollo organization id (hiring signal)."""
        client, _ = _client()
        return client.get_job_postings(org_id)
