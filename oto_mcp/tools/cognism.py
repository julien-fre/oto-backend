"""Cognism — B2B company/person search + reveal (email/phone) +
identity enrichment.

Classic connector (`kind="tools"`) on Cognism's synchronous REST API
(developers.cognism.com). LLM contract curated here; the HTTP client lives in
oto-core (`oto.tools.cognism.client.CognismClient`). Standard key cascade
(`resolve_api_key("cognism")`: BYO user > BYO org) — no platform mode
(key shared at org scale via BYO org, not an Otomata grant).

The filter DSL (`filters`) is a dict passed almost as is to Cognism — too
large (~150 fields, deep nesting) to be modeled field by field
on the tool side. Full reference: `cognism-filters` guide (`oto_guide`,
op=read, slug="cognism-filters"). CLOSED-value fields are validated
client-side BEFORE the network call (enum typo → explicit error, not a
silent empty page).

**Consolidated surface (ADR 0047 §Amendment, applied to the cognism connector)**:
9 → 6 tools. The homogeneous axis here is the **target** (contact = the person /
account = the company), not the verb: `search` and `redeem` take EXACTLY
the same parameters on both sides (`filters`/`index_size`/`last_returned_key`
on one hand, `ids`/`redeem_ids`/`merge_phones_and_locations` on the other), and
`entitlement` takes none. Hence `cognism_search`, `cognism_redeem` and
`cognism_entitlement`, the target in the `op` parameter.

⚠️ **The free/paid boundary is carried by the TOOL NAME, deliberately**:
`cognism_search` = FREE preview (`has*` flags, no real email/phone);
`cognism_redeem` = reveal **billed in credits**, the whole tool, no free op
inside. Grouping by business object (`cognism_contact(op=search|redeem|enrich)`)
would have drowned the one fact that costs money in a list of ops of a free
search tool — besides merging three disjoint parameter sets. Corollary:
**`op` has no default anywhere in this module** — on `cognism_redeem` because
no credit must be able to go out without an explicit intent, on the two
others because the targets are not interchangeable (the `filters` of a company
search does not have the same root as that of a contact search: a guessed target
would return an empty or wrong page, not an error).

Three tools stay STANDALONE:
- `cognism_enrich_contact` / `cognism_enrich_account`: DISJOINT variants —
  11 and 8 identity parameters of which only 3 in common (`linkedin_url`,
  `anchor_fields`, `min_match_score`); the rest (email/sha256/phone_number/
  job_title/account_name/account_website… vs name/website/domain/country/city)
  does not overlap, and even `min_match_score` does not have the same upstream default
  (30 contact / 40 account). Merged, they would weigh in the schema exactly what
  they weigh separately (criterion = parameter homogeneity, not the count).
- `cognism_filter_values`: discovery of the allowed values of a DYNAMIC filter
  field — its `kind` (technologies/regions/naics/…) is a vocabulary unrelated
  to the contact/account target, conflating it with `op` would create two meanings
  for one parameter. Same case as `zoho_modules`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `verify_key()` (already in the client — a `GET
    entitlement/contactEntitlementSubscription` call, written in oto-core for
    exactly this use: "Validate the key via an entitlement call. Raises the
    upstream HTTPError (401 = invalid key) on failure."). Bearer token, read with no
    side effect.

    **Authenticated ≠ usable** (class oto#69): does not distinguish scope
    here — the Contact entitlement is a BASE subscription, not one of the two
    targets (`contact`/`account`) that `_target()` already refuses upstream for an
    unknown op.
    """
    from oto.tools.cognism.client import CognismClient

    CognismClient(api_key=fields["key"]).verify_key()


# The connector's two TARGETS, values of `op`: the contact (the person) and
# the account (the company). Single source — input validation AND the
# refusal message derive from it, so an added target cannot be accepted without being
# announced (nor the reverse).
_TARGETS = ("contact", "account")
_TARGETS_ERROR = "op must be 'contact' or 'account'"

# Default page size AT COGNISM, which differs by target (25 contacts,
# 100 companies). `index_size=None` = "the target's default": freezing a single
# value would silently change pagination on one of the two sides.
_DEFAULT_INDEX_SIZE = {"contact": 25, "account": 100}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _target(op: str) -> str:
    """Validate `op` BEFORE any key resolution and any network call — an unknown
    target must never reach the client (hence never, via a derived path,
    consume a credit)."""
    if op not in _TARGETS:
        raise _bad(_TARGETS_ERROR)
    return op


def register(mcp: FastMCP) -> None:
    from oto.tools.cognism.client import CognismClient

    connector_verify.register("cognism", _verify)

    def _client() -> tuple[CognismClient, bool]:
        key, is_platform = access.resolve_api_key("cognism")
        return CognismClient(api_key=key), is_platform

    def _run(fn):
        """Run a Cognism call: translates an error into an actionable McpError
        (ValueError = invalid filter detected client-side, no network call;
        upstream 5xx = retry; 401 = invalid key; otherwise = Cognism error as
        is) and counts platform usage on success (platform mode
        currently not open for Cognism, de facto no-op)."""
        client, is_platform = _client()
        try:
            result = fn(client)
        except McpError:
            raise
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        except Exception as e:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if status and status >= 500:
                msg = (f"Cognism returned a server error ({status}). An upstream 5xx "
                       "does not prove an outage: first check the call's "
                       "parameters. If the input is correct: a single new "
                       "attempt, deferred.")
            elif status == 401:
                msg = "Cognism key invalid or revoked (401). Check the key that was set."
            else:
                msg = f"Cognism could not process the request ({e})."
            raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))
        if is_platform:
            access.record_platform_usage("cognism")
        return result

    @mcp.tool()
    def cognism_search(
        op: Literal["contact", "account"],
        filters: Optional[dict] = None,
        index_size: Optional[int] = None,
        last_returned_key: Optional[str] = None,
    ) -> dict:
        """Search the B2B database — contacts (people) or accounts (companies).
        FREE preview: SPENDS NO CREDITS (Cognism).

        `op` — the target. Required, no default: the two are NOT interchangeable
        (the `filters` dict does not have the same root, see below), so a guessed
        target would return an empty or wrong page rather than an error.
        - **"contact"**: search B2B contacts by name/title/seniority/location/
          company + more.
        - **"account"**: search B2B companies by name/domain/industry/headcount/
          technologies + more.

        Returns the raw Cognism page: `results[]` (contacts/companies with `has*`
        boolean flags — NOT real email/phone), `totalResults`, `lastReturnedKey`
        (cursor for the next page). Use `cognism_redeem` to reveal the real
        email/phone (contact) or the full record (account) for a match — that one
        SPENDS CREDITS, unlike this search.

        Args:
            op: contact | account.
            filters: nested filter dict matching Cognism's exact JSON shape.
                - op="contact": top-level contact fields (firstName, jobTitles,
                  seniority…) plus a nested `account` object for the employer's
                  firmographics (types, industries, headcount, technologies…).
                - op="account": the same firmographic fields, but AT THE ROOT
                  (no `account` prefix — the company IS the root object for this
                  endpoint).
                See the `cognism-filters` guide (oto_guide, op=read) for the full
                DSL (~150 fields) — closed-set fields are validated before the
                network call: op="contact" (seniority, jobFunctions,
                managementLevel, account.types, funding type/series, hiring
                department, sort_fields, accountSearchOptions), op="account"
                (types, funding type/series, hiring department,
                accountSearchOptions).
            index_size: page size, max 100. Omitted = Cognism's own default FOR
                THAT TARGET: 25 for op="contact", 100 for op="account".
            last_returned_key: cursor from the previous page's response. Empty
                = first page. Cognism paginates SEQUENTIALLY only — you cannot
                jump to an arbitrary page. Same for both targets.
        """
        target = _target(op)
        size = index_size if index_size is not None else _DEFAULT_INDEX_SIZE[target]
        if target == "contact":
            return _run(lambda c: c.search_contacts(
                filters, index_size=size, last_returned_key=last_returned_key,
            ))
        return _run(lambda c: c.search_accounts(
            filters, index_size=size, last_returned_key=last_returned_key,
        ))

    @mcp.tool()
    def cognism_redeem(
        op: Literal["contact", "account"],
        ids: Optional[list[str]] = None,
        redeem_ids: Optional[list[str]] = None,
        merge_phones_and_locations: bool = False,
    ) -> dict:
        """⚠️ SPENDS CREDITS. Reveal the full record of matches previously found
        with `cognism_search` — searching is free, THIS call is billed (Cognism).

        `op` — the target. Required, no default: nothing in this tool is free.
        - **"contact"**: reveal full contact data (real email/phone) for contacts
          found via `cognism_search(op="contact")`.
        - **"account"**: reveal full company data for accounts found via
          `cognism_search(op="account")`.

        Returns `{"total": <int>, "result": [<full contact/account records>]}`.

        Args:
            op: contact | account.
            ids: contact ids (op="contact") / account ids (op="account") from a
                prior search, OR…
            redeem_ids: redeemIds from a prior search (they encode contact +
                current job title + company — Cognism falls back to the current
                redeemId if this one is stale after a job change). Exactly one of
                `ids`/`redeem_ids` is required — mixing both in one call is not
                supported by Cognism.
            merge_phones_and_locations: merge the phones/locations arrays in the
                response.
        """
        target = _target(op)
        if not ids and not redeem_ids:
            raise _bad(f"cognism_redeem(op='{target}') requires `ids` or "
                       "`redeem_ids` — this call consumes credits, nothing is "
                       "guessed. Both come from a previous `cognism_search`.")
        if target == "contact":
            return _run(lambda c: c.redeem_contacts(
                ids=ids, redeem_ids=redeem_ids,
                merge_phones_and_locations=merge_phones_and_locations,
            ))
        return _run(lambda c: c.redeem_accounts(
            ids=ids, redeem_ids=redeem_ids,
            merge_phones_and_locations=merge_phones_and_locations,
        ))

    @mcp.tool()
    def cognism_enrich_contact(
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        sha256: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        phone_number: Optional[str] = None,
        job_title: Optional[str] = None,
        account_name: Optional[str] = None,
        account_website: Optional[str] = None,
        anchor_fields: Optional[list[str]] = None,
        min_match_score: Optional[int] = None,
    ) -> dict:
        """Find ONE best-match contact from identity details, no search step (Cognism).

        At least one identity field is required. Returns the matched contact
        (shape depends on your entitlement) with a match score, or an empty
        result if nothing scored above `min_match_score`.

        Args:
            email / sha256 / linkedin_url: unique identifiers — best accuracy
                alone.
            first_name + last_name + job_title, combined with account_name or
                account_website: second-best accuracy combo.
            phone_number: searched across all phone number types.
            anchor_fields: fields that MUST match for a result to be returned.
            min_match_score: minimum score to return a match (Cognism default
                30; below ~27 is considered low quality). Provide as many
                fields as you have — Cognism returns its best match.
        """
        return _run(lambda c: c.enrich_contact(
            first_name=first_name, last_name=last_name, email=email,
            sha256=sha256, linkedin_url=linkedin_url, phone_number=phone_number,
            job_title=job_title, account_name=account_name,
            account_website=account_website, anchor_fields=anchor_fields,
            min_match_score=min_match_score,
        ))

    @mcp.tool()
    def cognism_enrich_account(
        name: Optional[str] = None,
        website: Optional[str] = None,
        domain: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        country: Optional[str] = None,
        city: Optional[str] = None,
        anchor_fields: Optional[list[str]] = None,
        min_match_score: Optional[int] = None,
    ) -> dict:
        """Find ONE best-match company from identity details, no search step (Cognism).

        At least one identity field is required.

        Args:
            website / domain / linkedin_url: unique identifiers — best
                accuracy alone.
            name, combined with country or city (HQ or office): second-best
                accuracy combo.
            anchor_fields: fields that MUST match for a result to be returned.
            min_match_score: minimum score to return a match (Cognism default
                40 here — NOTE: different from `cognism_enrich_contact`'s
                default of 30; below ~35 is considered low quality for
                accounts).
        """
        return _run(lambda c: c.enrich_account(
            name=name, website=website, domain=domain, linkedin_url=linkedin_url,
            country=country, city=city,
            anchor_fields=anchor_fields, min_match_score=min_match_score,
        ))

    @mcp.tool()
    def cognism_entitlement(op: Literal["contact", "account"]) -> dict:
        """Which fields the configured Cognism key can see — check before assuming
        a field will come back populated (Cognism).

        `op` — the target. Required, no default.
        - **"contact"**: contact fields (email, phones, education, skills…).
        - **"account"**: account/company fields.

        Args:
            op: contact | account.
        """
        target = _target(op)
        if target == "contact":
            return _run(lambda c: c.contact_entitlement())
        return _run(lambda c: c.account_entitlement())

    @mcp.tool()
    def cognism_filter_values(
        kind: Literal["technologies", "managementLevels", "companySizes",
                      "industries", "jobFunctions", "regions", "countries",
                      "states", "sic", "isic", "naics", "skills",
                      "companyTypes", "seniority"],
        search: Optional[str] = None,
        index_size: int = 20,
        last_returned_key: Optional[str] = None,
    ) -> dict:
        """Allowed values for a DYNAMIC Cognism filter field (Cognism).

        Args:
            kind: one of "technologies", "managementLevels", "companySizes",
                "industries", "jobFunctions", "regions", "countries",
                "states", "sic", "isic", "naics", "skills", "companyTypes",
                "seniority". NOTE: seniority/jobFunctions/managementLevel are
                already validated client-side against a fixed list (see the
                `cognism-filters` guide) — you don't need this tool for those
                unless you suspect Cognism has updated the list.
            search: only for kind="technologies" (the one searchable/paginated
                list) — filters by substring.
            index_size, last_returned_key: pagination, kind="technologies" only.
        """
        return _run(lambda c: c.filter_values(
            kind, search=search, index_size=index_size,
            last_returned_key=last_returned_key,
        ))
