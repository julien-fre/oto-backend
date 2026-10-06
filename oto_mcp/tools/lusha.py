"""Lusha — contact search + enrich (email/phone reveal) in one call.

Key resolved per call via `access.resolve_api_key("lusha")` — byo-only
provider (user key set on /account, or the active org's shared credential).
No platform key.

Only one endpoint wired for now: search-and-enrich (up to 100
contacts per call, identified by email/LinkedIn/name+company/Lusha id).
Each contact can fail INDIVIDUALLY (`results[].error`:
NOT_FOUND, COMPLIANCE_RESTRICTED, ENRICH_FAILED) without failing the whole
call — this is NOT a "loop over single-record" bulk mode like
folk: Lusha natively accepts a batch in a single HTTP request,
hence no `_bulk_run` here.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

_MAX_CONTACTS_PER_CALL = 100
_REVEAL_VALUES = frozenset({"emails", "phones"})


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _verify(fields: dict, config: dict | None = None) -> dict:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth+quota`.

    `GET /account/usage`. What the Lusha docs establish: "the Account Usage
    endpoint itself has no charge for checking your balance (it's a utility
    endpoint for monitoring)" — written in black and white, not just an
    absence of counter.

    The balance (`remaining`) tells a dead key from an empty account —
    topping up is not reconnecting.
    """
    from oto.tools.lusha.client import LushaClient

    infos = LushaClient(api_key=fields["key"])._request("GET", "/account/usage") or {}
    restant = infos.get("remaining")
    if not isinstance(restant, int):
        raise RuntimeError(
            f"Lusha answered without a readable credit balance: {str(infos)[:200]}")
    if restant <= 0:
        raise connector_verify.QuotaEpuise(
            "The Lusha key is good, but the account is empty (0 credits "
            "left). Top up the account at Lusha — reconnecting would "
            "change nothing.")
    return {"quota": {"restant": restant, "unite": "credits"}}


def register(mcp: FastMCP) -> None:
    from oto.tools.lusha.client import LushaClient

    connector_verify.register("lusha", _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client() -> LushaClient:
        key, _ = access.resolve_api_key("lusha")
        return LushaClient(api_key=key)

    @mcp.tool()
    def lusha_search_and_enrich(
        contacts: list[dict],
        reveal: Optional[list[str]] = None,
        include_partial_profiles: Optional[bool] = None,
    ) -> dict:
        """Search for contacts and reveal their emails/phones in ONE call —
        up to 100 contacts per request.

        Returns —
            {requestId, results: [{id, firstName, lastName, fullName,
            jobTitle, location, tags, emails, phones, company, socialLinks,
            previousEmployment, updateDate, clientReferenceId, error?}],
            billing: {creditsCharged, resultsReturned}}. A contact Lusha
            couldn't resolve or reveal carries an `error: {code, message}`
            (NOT_FOUND | COMPLIANCE_RESTRICTED | ENRICH_FAILED) instead of
            the profile fields — inspect it PER-RESULT, a 200 response can
            still contain per-contact failures.

            ⚠️ Billing: TWO charges apply — one for the search (api_search)
            PLUS one per revealed field per contact.
            `billing.creditsCharged` is the actual total charged for THIS
            call — surface it back before repeating a large reveal.

        Args:
            contacts: one dict per contact to search, up to 100. Identify
                each by ANY combination of: `id` (a Lusha contact id),
                `linkedinUrl`, `email`, `firstName`+`lastName`+
                (`companyName` or `companyDomain`). `clientReferenceId` is
                an optional free-text tag you set yourself, echoed back on
                the matching result — use it to correlate results when you
                don't already have a stable Lusha `id`.
            reveal: which fields to unlock — "emails", "phones", or both.
                Omit to search/match contacts WITHOUT unlocking data
                (billed as search-only, see Returns).
            include_partial_profiles: include results Lusha considers
                incomplete rather than dropping them.
        """
        if not contacts:
            raise _bad("contacts: at least one contact required.")
        if len(contacts) > _MAX_CONTACTS_PER_CALL:
            raise _bad(
                f"{len(contacts)} contacts — Lusha caps search-and-enrich "
                f"at {_MAX_CONTACTS_PER_CALL} per call, split into several calls.")
        if reveal:
            unknown = set(reveal) - _REVEAL_VALUES
            if unknown:
                raise _bad(
                    f"reveal: unknown value(s) {sorted(unknown)} — "
                    f"expected among {sorted(_REVEAL_VALUES)}.")
        return _client().search_and_enrich(
            contacts, reveal=reveal, include_partial_profiles=include_partial_profiles)
