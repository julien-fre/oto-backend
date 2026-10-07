"""Hunter.io — emails by domain + email finder + verifier.

Key resolved per call: user key (`/account`) first, otherwise platform
key + daily quota (member). A guest must set their own key.
"""
from __future__ import annotations

from typing import Optional

import datetime as _dt

from fastmcp import FastMCP

from ..connectors import verify as connector_verify

from .. import access, output_projection


def _verify(fields: dict, config: dict | None = None) -> dict:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth+quota`.

    `GET https://api.hunter.io/v2/account`. What Hunter's docs establish, quoted:

    - **authenticated** — the key is required (`api_key` in the query, `X-API-KEY`, or
      `Authorization: Bearer`);
    - **no side effect** — a GET of account information;
    - **free, and here it is WRITTEN** — "All these calls are free.", from the
      "Account & API management" section. Unlike Folk and Pennylane, where we had to
      settle for the absence of a credit counter as a hint: here the docs
      state it.

    First `auth+quota` probe: it reads the BALANCE, not just the authentication.
    That is what lets it tell apart the two refusals a caller always confuses —
    a wrong key (which must be replaced) and an empty account (which must be
    topped up). Reconnecting in the second case is useless, and yet it is the
    reflex.

    The balance is RETURNED, never shown on the card: a displayed number would promise
    a freshness the platform only keeps by querying, and querying costs.
    The card carries the verdict and its date; the number lives in this test's
    response, with the instant it was read.

    **Authenticated ≠ usable** (class named on oto#69, cf. attio/pennylane):
    here the axis is not the SCOPE but the BALANCE — `QuotaEpuise` below IS the
    distinction, an empty key authenticates just fine and can no longer search anything.
    """
    from oto.tools.hunter.client import HunterClient

    infos = HunterClient(api_key=fields["key"]).account_info()
    data = (infos or {}).get("data") or {}
    if not data:
        raise RuntimeError(
            f"Hunter answered without account information: {str(infos)[:200]}")

    requetes = (data.get("requests") or {}).get("searches") or {}
    disponible, utilise = requetes.get("available"), requetes.get("used")
    if isinstance(disponible, int) and isinstance(utilise, int):
        restant = disponible - utilise
        if restant <= 0:
            raise connector_verify.QuotaEpuise(
                f"The Hunter key is valid, but the account is empty: "
                f"{utilise} searches used out of {disponible}. Top up the "
                "account at Hunter — reconnecting would change nothing.")
        return {"quota": {
            "restant": restant, "utilise": utilise, "inclus": disponible,
            # The UNIT, without which a bare number reads however one likes: these are
            # searches, not euros or calls.
            "unite": "recherches",
            # The INSTANT: this number ages as soon as it is read, and nothing
            # refreshes it until someone re-tests.
            "mesure_a": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }}
    # The balance is not readable: the key authenticates, we say so, and we do not
    # fabricate a quota we did not measure.
    return {}


def register(mcp: FastMCP) -> None:
    from oto.tools.hunter.client import HunterClient

    connector_verify.register("hunter", _verify,
                              couvre=connector_verify.AUTH_QUOTA)

    def _client() -> tuple[HunterClient, bool]:
        key, is_platform = access.resolve_api_key("hunter")
        return HunterClient(api_key=key), is_platform

    @mcp.tool()
    def hunter_domain_search(domain: str, limit: int = 10,
                             full: bool = False) -> dict:
        """List public emails found on a company domain (Hunter domain-search).

        Useful to discover existing email patterns and contacts.
        Cost: 1 Hunter credit per batch of 10 emails.

        Args:
            domain: Company domain (e.g. "example.com").
            limit: Max emails to return (1 credit per 10).
            full: return the per-address PROVENANCE too — `sources` (every page where
                the address was seen) and the `verification` detail. They dominate the
                payload and a contact sweep never reads them, hence dropped by
                default; ask for them when you must justify WHERE an address comes
                from (a real need under GDPR).
        """
        client, is_platform = _client()
        result = client.domain_search(domain=domain, limit=limit)
        if is_platform:
            access.record_platform_usage("hunter")
        # Tightened default (#36): a saving that has to be asked for serves no one
        # — measured, no agent ever passed the old `compact=True`.
        if not full:
            result = output_projection.project(
                result, items_path="data.emails",
                item_drop=("sources", "verification"))
        return result

    @mcp.tool()
    def hunter_email_finder(
        domain: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        full_name: Optional[str] = None,
    ) -> dict:
        """Find a specific person's email at a company (Hunter email-finder).

        Provide either (`first_name` + `last_name`) or `full_name`.
        Cost: 1 Hunter credit per call.
        """
        client, is_platform = _client()
        result = client.email_finder(
            domain=domain, first_name=first_name, last_name=last_name, full_name=full_name,
        )
        if is_platform:
            access.record_platform_usage("hunter")
        return result

    @mcp.tool()
    def hunter_email_verify(email: str) -> dict:
        """Verify a single email's deliverability (Hunter email-verifier).

        Cost: 1 Hunter credit per call.
        """
        client, is_platform = _client()
        result = client.email_verifier(email=email)
        if is_platform:
            access.record_platform_usage("hunter")
        return result
