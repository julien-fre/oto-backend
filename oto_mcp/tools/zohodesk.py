"""Zoho Desk — support tickets, threads, contacts, articles (Help Center KB).

Credential = OAuth2 (self-client) with 5 fields: client_id + client_secret +
refresh_token + org_id (`orgId` header required) + data_center (region, non-secret)
→ generic multi-field model (ADR 0011), resolved per call via
`access.resolve_credential_fields("zohodesk")`. byo_user. Access token derived/cached
in memory on the client side.
"""
from __future__ import annotations

from typing import Optional

import requests
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, status_hints
from ..connectors import verify as connector_verify

# Zoho hosts per regional data center: the Desk API AND the OAuth refresh are tied to
# their issuing region (a `.eu` self-client hitting `desk.zoho.com`/`accounts.zoho.com`
# is rejected with an opaque `invalid_client` — same gotcha as the CRM connector). The
# credential's `data_center` field selects the API domains (`desk.zoho.<tld>`) and
# OAuth domains (`accounts.zoho.<tld>`). Recognized regions:
_DC_DOMAINS = {
    "com": ("https://desk.zoho.com", "https://accounts.zoho.com"),
    "eu": ("https://desk.zoho.eu", "https://accounts.zoho.eu"),
    "in": ("https://desk.zoho.in", "https://accounts.zoho.in"),
    "au": ("https://desk.zoho.com.au", "https://accounts.zoho.com.au"),
    "jp": ("https://desk.zoho.jp", "https://accounts.zoho.jp"),
    "ca": ("https://desk.zohocloud.ca", "https://accounts.zohocloud.ca"),
}


def _resolve_dc_domains(data_center: Optional[str]) -> tuple[str, str]:
    """`(api_domain, accounts_url)` for the declared Zoho Desk region. Missing
    or unrecognized region → actionable `McpError`, **never** a silent fallback to `com`
    (that fallback masked the real cause of an `invalid_client`: self-client set up on
    another region). `com` remains fully valid — we just require a recognized choice."""
    dc = (data_center or "").strip().lower()
    if dc not in _DC_DOMAINS:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=(
            (f"Unrecognized Zoho data center: {data_center!r}." if dc
             else "Zoho data center missing.")
            + " Fill in your region in the \"Data center\" field of the Zoho Desk connector —"
            " one of: com, eu, in, au, jp, ca. It is visible in the URL when you are"
            " signed in to Zoho Desk (e.g. desk.zoho.eu → \"eu\", desk.zoho.com → \"com\")."
        )))
    return _DC_DOMAINS[dc]


# Desk surface → OAuth scope that unlocks it. Serves the probe AND the diagnosis: a
# raw `SCOPE_MISMATCH` from Zoho does NOT say which scope is missing (feedback #299),
# while that is the only information needed to regenerate the self-client.
_DESK_SCOPES = (
    ("departments", "Desk.basic.READ", lambda c: c.list_departments()),
    ("tickets", "Desk.tickets.READ", lambda c: c.list_tickets(limit=1)),
    ("contacts", "Desk.contacts.READ", lambda c: c.list_contacts(limit=1)),
    ("articles (KB)", "Desk.articles.READ", lambda c: c.list_articles(limit=1)),
)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (probe contract)
    """Probe with NO side effects, in two steps — same pattern as the CRM connector.

    1. **OAuth refresh**: validates client_id + client_secret + refresh_token + region.
    2. **real read of each Desk surface** (`per_page`/`limit` = 1): a Zoho
       token can authenticate with PARTIAL scopes — a real case, articles
       answered 200 while tickets/contacts/departments returned an opaque
       `SCOPE_MISMATCH`. No readable surface ⇒ failure, citing the granted scope
       and the missing ones (the credential is unusable). At least one
       readable ⇒ success: the credential works, even if restricted.
    """
    from oto.tools.zohodesk.client import ZohoDeskClient

    from .zoho import _zoho_error_hint  # same family, single source of the diagnosis

    status_hints.require_complete("zohodesk", fields)
    api_domain, accounts_url = _resolve_dc_domains(fields.get("data_center"))
    try:
        tok = requests.post(f"{accounts_url}/oauth/v2/token", data={
            "grant_type": "refresh_token",
            "client_id": fields.get("client_id"),
            "client_secret": fields.get("client_secret"),
            "refresh_token": fields.get("refresh_token"),
        }, timeout=20).json()
    except Exception as e:  # noqa: BLE001 — network / unreadable response
        raise ValueError(f"Zoho Desk connection failed: {type(e).__name__}") from e
    if "access_token" not in tok:
        raise ValueError(_zoho_error_hint(tok.get("error") or tok))
    granted = tok.get("scope", "")

    client = ZohoDeskClient(
        client_id=fields.get("client_id"), client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"), org_id=fields.get("org_id"),
        api_domain=api_domain, accounts_url=accounts_url,
    )
    missing: list[str] = []
    for label, scope, call in _DESK_SCOPES:
        try:
            call(client)
            return  # at least one readable surface → credential usable
        # noqa: SILENT — missing scope collected then returned in the probe message
        except Exception as e:  # noqa: BLE001 — the provider error IS the probe's return
            if "SCOPE" in str(e).upper():
                missing.append(f"{label} → {scope}")
    if missing:
        extra = f" (granted scope: {granted})" if granted else ""
        raise ValueError(
            "the token authenticates but opens NO Zoho Desk surface" + extra
            + " — expected scopes: " + " ; ".join(missing)
            + ". Regenerate the Desk self-client with these scopes.")
    raise ValueError("Zoho Desk connection established but no readable surface "
                     "(wrong org_id, or departments/tickets inaccessible).")


def register(mcp: FastMCP) -> None:
    connector_verify.register("zohodesk", _verify)
    from oto.tools.zohodesk.client import ZohoDeskClient

    def _client() -> ZohoDeskClient:
        creds = access.resolve_credential_fields("zohodesk")
        api_domain, accounts_url = _resolve_dc_domains(creds.get("data_center"))
        return ZohoDeskClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            org_id=creds.get("org_id"),
            api_domain=api_domain,
            accounts_url=accounts_url,
        )

    @mcp.tool()
    def zohodesk_tickets(
        from_index: int = 1,
        limit: int = 50,
        department_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        """List support tickets.

        Args:
            status: Open | On Hold | Escalated | Closed.
            sort_by: a field name (prefix with "-" for descending).
        """
        return _client().list_tickets(
            from_index=from_index, limit=limit, department_id=department_id,
            status=status, sort_by=sort_by)

    @mcp.tool()
    def zohodesk_ticket(ticket_id: str, include: Optional[str] = None) -> dict:
        """Get one ticket. `include` = contacts,products,assignee,team…"""
        return _client().get_ticket(ticket_id, include=include)

    @mcp.tool()
    def zohodesk_search_tickets(
        query: dict, from_index: int = 1, limit: int = 50,
    ) -> dict:
        """Search tickets. `query` = dict of field=value pairs (Zoho search params)."""
        return _client().search_tickets(query, from_index=from_index, limit=limit)

    @mcp.tool()
    def zohodesk_create_ticket(data: dict) -> dict:
        """Create a ticket. Required: subject, departmentId, contactId (or contact)."""
        return _client().create_ticket(data)

    @mcp.tool()
    def zohodesk_update_ticket(ticket_id: str, data: dict) -> dict:
        """Patch ticket fields (status, priority, assignee, customFields…)."""
        return _client().update_ticket(ticket_id, data)

    @mcp.tool()
    def zohodesk_ticket_threads(ticket_id: str) -> dict:
        """List the threads (replies/comments) of a ticket."""
        return _client().list_threads(ticket_id)

    @mcp.tool()
    def zohodesk_contacts(from_index: int = 1, limit: int = 50) -> dict:
        """List Desk contacts."""
        return _client().list_contacts(from_index=from_index, limit=limit)

    @mcp.tool()
    def zohodesk_create_contact(data: dict) -> dict:
        """Create a Desk contact. Required: lastName. Optional: firstName, email, phone."""
        return _client().create_contact(data)

    @mcp.tool()
    def zohodesk_departments() -> dict:
        """List Desk departments."""
        return _client().list_departments()

    @mcp.tool()
    def zohodesk_articles(
        from_index: int = 1,
        limit: int = 50,
        department_id: Optional[str] = None,
        category_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        """List Help Center (KB) articles — metadata only (the HTML body comes
        from `zohodesk_article`).

        Args:
            status: Published | Draft | Review | Expired.
            sort_by: a field name (e.g. modifiedTime, viewCount; prefix "-" for desc).
        """
        return _client().list_articles(
            from_index=from_index, limit=limit, department_id=department_id,
            category_id=category_id, status=status, sort_by=sort_by)

    @mcp.tool()
    def zohodesk_article(article_id: str) -> dict:
        """Get one Help Center article, including its full HTML body (`answer`)."""
        return _client().get_article(article_id)
