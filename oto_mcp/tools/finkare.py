"""Finkare — AI-driven receivables collection (invoices, debtors, dunning).

Four verbs, one per resource of the v1 API: the invoice to collect, the debtor,
the payment, and the dunning workflow that ties them together. Key resolved per call
(member → org), never a platform key: a Finkare key opens the receivables of ONE
company, pooling it would make no sense.

⚠️ **Amounts are in CENTS**, everywhere, on input as on output. An
`amountCents: 150000` is an invoice of €1,500. This is the costliest trap of
this API: the same value read as euros goes unnoticed and skews every dunning run.

⚠️ **The key carries its environment**: `fk_test_…` works on the sandbox,
`fk_live_…` on real receivables. Nothing to choose at call time — the client derives
the address from the key set. A test key therefore cannot chase a real debtor.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET invoices` (already in the client — `list_invoices`), Finkare exposing
    neither `/me` nor a balance. Read with no side effect; an EMPTY list (no
    receivable) is a normal state, never a refusal.

    **Authenticated ≠ usable** (class oto#69): does not distinguish scope —
    a Finkare key carries the entire receivables scope of ONE company.
    """
    from oto.tools.finkare import FinkareClient

    FinkareClient(api_key=fields["key"]).list_invoices()


def register(mcp: FastMCP) -> None:
    from oto.tools.finkare import FinkareClient

    connector_verify.register("finkare", _verify)

    def _client() -> FinkareClient:
        key, _ = access.resolve_api_key("finkare")
        return FinkareClient(api_key=key)

    @mcp.tool()
    def finkare_invoice(
        op: Literal["list", "create", "bulk", "cancel"] = "list",
        invoice: Optional[dict] = None,
        invoices: Optional[list] = None,
        invoice_id: Optional[str] = None,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """The receivables to collect — submit them, list them, stop one.

        `op`:
        - "list": the inventory, filterable by `status`, paginated (`limit` ≤ 100).
        - "create" (`invoice`): ONE receivable. Required: `invoiceNumber`,
          `amountCents` (⚠️ **cents**), `dueDate` (ISO 8601), and a `debtor`
          object with at least `name` and `email` — `siret`, `address`, `city`,
          `postalCode` improve identification and letters.
        - "bulk" (`invoices`): batch import. The API refuses more than **100
          receivables** or 10 MB per request — split upstream, not after the refusal.
        - "cancel" (`invoice_id`, `reason`): **stops the dunning workflow** of
          this receivable. This is not a deletion: the invoice stays, the
          reminders stop. `reason` goes into the history — filling it in is what
          will make the stop understandable three months from now.

        ⚠️ Writes carry an idempotency key: the same call replayed after
        a network cut does not create a second invoice.
        """
        c = _client()
        if op == "create":
            if not invoice:
                raise ValueError("op=create requires `invoice` (the receivable object).")
            return c.create_invoice(invoice)
        if op == "bulk":
            if not invoices:
                raise ValueError("op=bulk requires `invoices` (the list of receivables).")
            return c.create_invoices_bulk(invoices)
        if op == "cancel":
            if not invoice_id:
                raise ValueError("op=cancel requires `invoice_id`.")
            return c.cancel_invoice(invoice_id, reason=reason)
        return c.list_invoices(status=status, page=page, limit=limit)

    @mcp.tool()
    def finkare_debtor(
        op: Literal["list", "get", "create", "update", "invoices", "score"] = "list",
        debtor_id: Optional[str] = None,
        debtor: Optional[dict] = None,
        search: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """The debtors — who owes what, and with what payment behavior.

        `op`:
        - "list": text search via `search`, paginated (`limit` ≤ 100).
        - "get" / "create" / "update" (`debtor_id`, `debtor`): the record. `name` and
          `email` are required; `siret` is 14 digits; `country` is a two-letter
          ISO code (FR by default server-side). `notes` stays **internal**
          — nothing written there goes to the debtor.
        - "invoices": all of this debtor's receivables, at once.
        - "score": the payment-behavior score computed by Finkare.
          ⚠️ It is a reading of THEIR model, not a judgment by oto: to be quoted as
          such if it is handed to someone.
        """
        c = _client()
        if op in ("get", "invoices", "score", "update") and not debtor_id:
            raise ValueError(f"op={op} requires `debtor_id`.")
        if op == "get":
            return c.get_debtor(debtor_id)
        if op == "create":
            if not debtor:
                raise ValueError("op=create requires `debtor` (the record).")
            return c.create_debtor(debtor)
        if op == "update":
            if not debtor:
                raise ValueError("op=update requires `debtor` (the fields to write).")
            return c.update_debtor(debtor_id, debtor)
        if op == "invoices":
            return c.debtor_invoices(debtor_id)
        if op == "score":
            return c.debtor_score(debtor_id)
        return c.list_debtors(search=search, page=page, limit=limit)

    @mcp.tool()
    def finkare_payment(
        op: Literal["list", "get", "by_invoice", "stats"] = "list",
        payment_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        period: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """The payments — READ-only: Finkare records them, oto does not post them.

        `op`:
        - "list": filterable by `status` (pending|completed|failed|refunded), by
          window (`from_date` / `to_date`, ISO 8601) or by `invoice_id`.
        - "get" (`payment_id`): one specific payment.
        - "by_invoice" (`invoice_id`): everything collected on a receivable —
          this is what says whether a balance remains, not the invoice status.
        - "stats" (`period` = day|week|month|year): the aggregates.
          ⚠️ That call requires the `reports:read` scope, which the others do not:
          a key that reads payments may fail here, and that is not an outage.
        """
        c = _client()
        if op == "get":
            if not payment_id:
                raise ValueError("op=get requires `payment_id`.")
            return c.get_payment(payment_id)
        if op == "by_invoice":
            if not invoice_id:
                raise ValueError("op=by_invoice requires `invoice_id`.")
            return c.invoice_payments(invoice_id)
        if op == "stats":
            return c.payment_stats(period=period)
        return c.list_payments(status=status, from_date=from_date, to_date=to_date,
                               invoice_id=invoice_id, page=page, limit=limit)

    @mcp.tool()
    def finkare_workflow(
        op: Literal["status", "history", "next_action", "trigger", "stats"] = "status",
        invoice_id: Optional[str] = None,
        action: Optional[Literal["start", "pause", "resume", "cancel",
                                 "escalate"]] = None,
        reason: Optional[str] = None,
    ) -> dict:
        """The dunning of a receivable: where it stands, what comes next, and how to steer it.

        `op`:
        - "status" / "history" / "next_action" (`invoice_id`): the current state, what
          has already been tried, and the next planned action with its date.
        - "trigger" (`invoice_id`, `action`, `reason`): **acts on a real
          debtor**. `start` launches the cascade, `pause` suspends it, `resume`
          resumes it, `cancel` stops it, `escalate` moves up a notch.
        - "stats": the aggregates of all workflows.

        ⚠️ **`trigger` produces EXTERNAL, irreversible effects** — a letter
        that has gone out cannot be recalled, an escalation seen by the debtor cannot be undone.
        Call only on explicit request, and `reason` deserves to be filled in:
        it is what someone will read to understand why the dunning stopped
        there.
        """
        c = _client()
        if op == "stats":
            return c.workflow_stats()
        if not invoice_id:
            raise ValueError(f"op={op} requires `invoice_id`.")
        if op == "history":
            return c.workflow_history(invoice_id)
        if op == "next_action":
            return c.workflow_next_action(invoice_id)
        if op == "trigger":
            if not action:
                raise ValueError(
                    "op=trigger requires `action`: start|pause|resume|cancel|escalate.")
            return c.workflow_trigger(invoice_id, action, reason=reason)
        return c.workflow_status(invoice_id)
