"""GoCardless — SEPA direct debits (read-only).

Key resolved per call via `access.resolve_api_key("gocardless")`: per-user
key model (like Pennylane/Attio), no platform key. Each
user sets their own GoCardless key — their direct debits are
visible only to them.

Strictly read-only surface: GoCardless is a source (reconciliation,
handling of failures). No mutation exposed — an agent cannot
cancel a direct debit.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ALONE.

    ⚠️ `GoCardlessClient.fetch()` NEVER RAISES on an HTTP refusal — it returns a
    dict `{"error", "status_code", "details"}` (the client's choice, for its
    pagination loops, which must be able to stop cleanly rather than
    raise in the middle of a collection). `list_creditors()` (already in the client —
    the token's merchant accounts, the closest thing to an identity at
    GoCardless) inherits the same silence: without reading that dict and raising ourselves,
    a dead token would answer `ok:true` — a new class, distinct from the three
    already named in the issue (a CLIENT that never fails by exception,
    whatever the upstream HTTP code).

    **Authenticated ≠ usable** (class oto#69): does not distinguish scopes —
    a GoCardless token (live or sandbox) carries the account's entire scope.
    """
    from oto.tools.gocardless import GoCardlessClient

    infos = GoCardlessClient(api_key=fields["key"]).fetch("creditors")
    if "error" not in infos:
        return
    code = infos.get("status_code")
    detail = str(infos.get("details") or infos["error"])[:300]
    if code in (401, 403):
        raise connector_verify.NonAutorise(f"GoCardless HTTP {code}: {detail}")
    raise RuntimeError(f"GoCardless: {detail}")


# Default view of a payout: enough to recognize it on the statement and
# reconcile it. `fx`, `tax_currency`, `metadata` and `links` come back on `full=True`.
_PAYOUT_KEYS = ("id", "amount", "deducted_fees", "currency", "status",
                "arrival_date", "created_at", "reference")


def _slim_payout(payout: dict) -> dict:
    return {k: payout.get(k) for k in _PAYOUT_KEYS}


def register(mcp: FastMCP) -> None:
    from oto.tools.gocardless import GoCardlessClient

    connector_verify.register("gocardless", _verify)

    def _client() -> GoCardlessClient:
        # Access guard = credential resolution: a key set at the accounting
        # (team) level only resolves for its members (ADR 0053 D1). No more
        # `require_namespace`: gocardless is not grant_only in the registry → it was
        # a no-op (ADR 0031).
        key, _is_platform = access.resolve_api_key("gocardless")
        return GoCardlessClient(api_key=key)

    @mcp.tool()
    def gocardless_creditors() -> list:
        """GoCardless merchant accounts (collecting account)."""
        return _client().list_creditors()

    @mcp.tool()
    def gocardless_payments(
        status: Optional[str] = None,
        limit: int = 50,
        mandate: Optional[str] = None,
        customer: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list:
        """List of direct debits (1 page).

        Args:
            status: failed, confirmed, paid_out, submitted, cancelled, charged_back…
            limit: page size (max 500).
            mandate: filter by mandate (MD…).
            customer: filter by customer (CU…).
            since: ISO8601, direct debits created after this date.
        """
        return _client().list_payments(
            status=status, limit=limit, mandate=mandate,
            customer=customer, created_gt=since,
        )

    @mcp.tool()
    def gocardless_payment(payment_id: str) -> dict:
        """Raw detail of a direct debit (PM…)."""
        return _client().get_payment(payment_id)

    @mcp.tool()
    def gocardless_payouts(
        status: Optional[str] = None,
        limit: int = 50,
        currency: Optional[str] = None,
        reference: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Grouped payouts received in the bank (payouts, PO…), 1 page.

        Amounts in CENTS: `amount` = net paid out, `deducted_fees` = fees
        already withheld. The payment-by-payment detail: `gocardless_payout`.

        Args:
            status: pending, paid, bounced.
            limit: page size (max 500).
            currency: EUR, GBP…
            reference: exact label of the transfer on the bank statement.
            since / until: ISO8601, bounds (exclusive) on the payout's
                creation — a bare date means midnight UTC. To go back further
                than one page, move `until` closer to the oldest returned.
            full: True = the raw object (fx, taxes, metadata, links).
        """
        rows = _client().list_payouts(
            status=status, limit=limit, currency=currency, reference=reference,
            created_gt=since, created_lt=until,
        )
        return {"payouts": rows if full else [_slim_payout(r) for r in rows]}

    @mcp.tool()
    def gocardless_payout(payout_id: str) -> dict:
        """A payout (PO…) and ALL its lines, to match it.

        `items`: one line per movement — `type` (payment_paid_out,
        payment_failed, payment_charged_back, payment_refunded, refund,
        refund_funds_returned, gocardless_fee, app_fee, revenue_share,
        surcharge_fee), signed `amount` in CENTS, `links.payment` (PM…) to
        pass to `gocardless_payment_party` for the customer. GoCardless only serves
        the lines of payouts created less than 6 months ago (HTTP 410
        beyond that).
        """
        return _client().payout_detail(payout_id)

    @mcp.tool()
    def gocardless_events(
        payment: Optional[str] = None,
        mandate: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """Events timeline. Failure reason: action='failed' on a payment."""
        return _client().list_events(
            payment=payment, mandate=mandate, action=action, limit=limit,
        )

    @mcp.tool()
    def gocardless_payment_party(payment_id: str) -> dict:
        """Resolves payment → mandate → customer (email, company, metadata flattened).

        ⚠️ GoCardless metadata may not carry an external customer
        identifier (depending on the merchant).
        """
        return _client().payment_party(payment_id)

    @mcp.tool()
    def gocardless_failure_reason(payment_id: str) -> dict:
        """Reason for a direct debit's latest failure (cause, description,
        will_attempt_retry). If will_attempt_retry is True, GoCardless will
        retry — do not issue a credit note until it is False."""
        return _client().failure_reason(payment_id)

    @mcp.tool()
    def gocardless_failed(
        since: Optional[str] = None,
        limit: int = 200,
    ) -> list:
        """Rejected direct debits, enriched, in a single call.

        Returns one row per failure with customer (name/email), amount,
        charge_date, failed_at, cause/reason_code, will_attempt_retry and
        mandate state — the payment→mandate→customer chain + the reason are
        resolved server-side. Sorted by failure date, descending.

        ⚠️ Facts only, no action decided: "retry vs redo a
        mandate" remains a business judgment (agent/guide). And as long as
        will_attempt_retry is True, issue nothing — GoCardless will retry.

        Args:
            since: ISO8601 (e.g. '2026-05-25'). Filter on created_at: a
                payment created before but failed after does not show up.
            limit: page size of the failed to enrich (max 500).
        """
        return _client().failed_payments(since=since, limit=limit)
