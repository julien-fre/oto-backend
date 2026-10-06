"""Nextmotion — sales: quotes, invoices (and credit notes), payments, revenue
statistics, and a patient's financial totals.

Sibling module of `nextmotion.py` (cf. `Connector.modules`). Five tools, because their
parameters do not overlap (ADR 0047):

- `nextmotion_quote` — list | get | update | delete | validate. A quote is only CREATED
  under a consultation (medical): no creation here.
- `nextmotion_invoice` — list | get | update | validate | pay | credit_note ; invoice
  period filter applied TOOL-SIDE → `nextmotion_periode` (not on quotes:
  `OApiQuote` has no `invoiced_time`, its `issued_time` is nullable). Neither creation
  (under a consultation only) nor deletion (an accounting document is corrected by
  a credit note);
- `nextmotion_payment` — list | get | update. A payment EMBEDS its whole invoice,
  hence its patient: the invoice goes through the same allowlist as
  `nextmotion_invoice`. A payment cannot be deleted (accounting document).
- `nextmotion_statistics` — `kind` x a period: clinic aggregates, no
  patient. `meta` (free-form object, not described by the spec) and `label_field` (undocumented
  parameter, which could change what the labels name) are not served.
- `nextmotion_patient_stats` — the totals of ONE patient designated by their id: quotes,
  invoiced, paid, credit notes, refunds, first and last visit dates. Nothing that
  identifies them; neither the opening date of their file nor their photo activity (the life of the
  medical file).

Every write has `dry_run=True` by default and its `data` goes through the input allowlist
(`nextmotion_entrees`); its response goes back through the resource allowlist.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import nextmotion_periode as periode
from .nextmotion_entrees import (_IN_CREDIT_NOTE, _IN_INVOICE, _IN_INVOICE_VALIDATE,
                                 _IN_PAY, _IN_PAYMENT, _IN_QUOTE, _IN_QUOTE_VALIDATE)
from .nextmotion_garde import (Kind, Write, _bad, _client, _need, _paging, _refuse_ignored,
                               _run, _serve_write, _uuid)
from .nextmotion_socle import (_CHART, _CREDIT_NOTE, _PATIENT_STATS, _PAYMENT, _WITHHELD,
                               _invoice, _one, _page, _quote, _shape)

_payment = _shape(_PAYMENT)
_chart = _shape(_CHART)
_patient_stats = _shape(_PATIENT_STATS)

def _invoice_of_credit_note(c, _, body):
    """The preview of a credit note: the invoice it points to, if it points to one."""
    invoice_id = (body or {}).get("invoice")
    if invoice_id is None:
        return {}
    _uuid("credit_note", "data.invoice", invoice_id)
    return _one(_run(lambda: c.get_invoice(invoice_id)), "invoice", _invoice)


_QUOTES = {"quote": Kind(
    "quotes", _quote, lire=lambda c, i: c.get_quote(i), withheld=_WITHHELD,
    writes={"update": Write(lambda c, i, b: c.update_quote(i, body=b), "item", _IN_QUOTE),
            "delete": Write(lambda c, i, b: c.delete_quote(i)),
            "validate": Write(lambda c, i, b: c.validate_quote(i, body=b), "item",
                              _IN_QUOTE_VALIDATE, optional_body=True)})}
_INVOICES = {"invoice": Kind(
    "invoices", _invoice, lire=lambda c, i: c.get_invoice(i), withheld=_WITHHELD,
    writes={"update": Write(lambda c, i, b: c.update_invoice(i, body=b), "item",
                            _IN_INVOICE),
            "validate": Write(lambda c, i, b: c.validate_invoice(i, body=b), "item",
                              _IN_INVOICE_VALIDATE, optional_body=True),
            "pay": Write(lambda c, i, b: c.pay_invoice(i, body=b), "item", _IN_PAY),
            "credit_note": Write(lambda c, cid, b: c.create_credit_note(cid, body=b),
                                 "clinic", _IN_CREDIT_NOTE, ("patient", "items"),
                                 current=_invoice_of_credit_note, key="credit_note",
                                 shape=_shape(_CREDIT_NOTE))})}
_PAYMENTS = {"payment": Kind(
    "payments", _payment, lire=lambda c, i: c.get_payment(i), withheld=_WITHHELD,
    writes={"update": Write(lambda c, i, b: c.update_payment(i, body=b), "item",
                            _IN_PAYMENT)})}

_STATISTICS = {
    "appointment_income": lambda c, cid, **kw: c.get_appointment_income_statistics(cid, **kw),
    "treatment_types": lambda c, cid, **kw: c.list_treatment_type_statistics(cid, **kw),
    "treatment_types_income": lambda c, cid, **kw: c.list_treatment_type_income_statistics(
        cid, **kw),
}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_quote(
        op: Literal["list", "get", "update", "delete", "validate"] = "list",
        clinic_id: Optional[str] = None,
        quote_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Quotes (devis) of a Nextmotion clinic — number, status, lines (sub-pricings,
        markup, accounting codes), totals, follow-up and channel. Health data, titles
        and free text are withheld; the patient is served by ID ONLY — resolve it with
        `nextmotion_patient(op="get")` when needed.

        Status codes: 1 NEW, 2 QUOTED, 3 ACCEPTED, 4 REJECTED, 5 INVOICED, 6 ACQUAINTED.

        `op`: "list" (default, `clinic_id`, optional `patient_id`) | "get" | "update"
        (`data`: rebate, title, free text, dates, follow-up fields…) | "validate" (draft
        → validated quote, optional `data`) | "delete" — all by `quote_id`. Lines
        cannot be edited here (each carries a clinical treatment id), nor can a quote
        be created (Nextmotion creates it under a consultation). `data` takes the fields the
        Nextmotion spec accepts for the op; any other field is refused.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | update | delete | validate.
            clinic_id: op="list".
            quote_id: every op but list.
            patient_id: op="list" — only this patient's quotes.
            data: op="update"/"validate" — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op in ("update", "delete", "validate"):
            return _serve_write(
                _QUOTES, "quote", op, client=_client, clinic_id=clinic_id,
                item_id=quote_id, data=data, dry_run=dry_run, item_name="quote_id",
                unused={"patient_id": patient_id, "limit": limit, "offset": offset,
                        "fields": fields})
        _refuse_ignored(op, data=data, dry_run=dry_run)
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, quote_id=quote_id)
            return _page(_run(lambda: c.list_quotes(
                clinic_id, patient_id=patient_id, **_paging(limit, offset))),
                "quotes", _quote, fields=fields)
        if op == "get":
            _need(op, quote_id=quote_id)
            _refuse_ignored(op, clinic_id=clinic_id, patient_id=patient_id,
                            limit=limit, offset=offset, fields=fields)
            return _one(_run(lambda: c.get_quote(quote_id)), "quote", _quote)
        raise _bad("op must be 'list', 'get', 'update', 'delete' or 'validate'.")

    @mcp.tool()
    def nextmotion_invoice(
        op: Literal["list", "get", "update", "validate", "pay", "credit_note"] = "list",
        clinic_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        invoiced_from: Optional[str] = None,
        invoiced_to: Optional[str] = None,
        max_pages: Optional[int] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Invoices of a Nextmotion clinic — number, status, lines, totals, payment
        methods. Health data, titles and free text are withheld; the patient is served
        by ID ONLY — resolve it with `nextmotion_patient(op="get")` when needed.

        Status codes: 2 NEW, 3 VALIDATED, 4 NEW_ONLY_DEPOSITS, 5 NEW_ISSUED,
        6 NEW_DEPOSITS_PAID, 9 ONLY_DEPOSITS, 10 ISSUED, 11 DEPOSITS_PAID, 12 NEUTRALIZED.

        Period filter (op="list"): `invoiced_from` / `invoiced_to` (YYYY-MM-DD, inclusive,
        on `invoiced_time`). Nextmotion neither filters nor orders, so the tool reads
        EVERY page (100 per call, up to `max_pages`) and says `pages_lues`,
        `factures_parcourues`, `complet`. `complet: false` = PARTIAL: call again with
        `offset=offset_suivant` and the same period. With a period, `limit` is refused
        and `offset` is where the scan starts. No consumables nor lots per invoice
        (`nextmotion_product` is not linked to invoices). Payments:
        `nextmotion_payment(invoice_id=…)`.

        `op` writes: "update" (`invoice_id` + `data`: rebate, title, free text, dates) |
        "validate" (draft → validated, optional `issued_time` / `invoiced_time`) | "pay"
        (`invoice_id` + amounts per means) | "credit_note" (`clinic_id` + `data`,
        `patient` and `items` [{name, amount, vat_rate…}] required; preview shows the
        `invoice` it names). Lines cannot be edited here (each carries a clinical
        treatment id); no create (under a consultation only), no delete (issue a credit
        note). `data` takes the fields the Nextmotion spec accepts for the op; any other
        field is refused.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | update | validate | pay | credit_note.
            clinic_id: op="list"/"credit_note".
            invoice_id: op="get"/"update"/"validate"/"pay".
            invoiced_from / invoiced_to: op="list" — period bounds, YYYY-MM-DD.
            max_pages: op="list" with a period — page cap, 1..100 (default 20).
            data: writes — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op in ("update", "validate", "pay", "credit_note"):
            return _serve_write(
                _INVOICES, "invoice", op, client=_client, clinic_id=clinic_id,
                item_id=invoice_id, data=data, dry_run=dry_run, item_name="invoice_id",
                unused={"invoiced_from": invoiced_from, "invoiced_to": invoiced_to,
                        "max_pages": max_pages, "limit": limit, "offset": offset,
                        "fields": fields})
        _refuse_ignored(op, data=data, dry_run=dry_run)
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, invoice_id=invoice_id)
            if invoiced_from is None and invoiced_to is None:
                if max_pages is not None:
                    raise _bad(f"op={op!r} does not use `max_pages` without "
                               "`invoiced_from`/`invoiced_to`.")
                return _page(_run(lambda: c.list_invoices(
                    clinic_id, **_paging(limit, offset))), "invoices", _invoice,
                    fields=fields)
            if limit is not None:
                raise _bad(f"op={op!r} does not use `limit` with a period: all the "
                           "invoices of the period are read and returned; `offset` is "
                           "the starting point of the scan there.")
            return _run(lambda: periode.lister(
                lambda o: c.list_invoices(clinic_id, limit=periode.PAGE, offset=o),
                invoiced_from, invoiced_to, offset=0 if offset is None else offset,
                max_pages=max_pages, fields=fields))
        if op == "get":
            _need(op, invoice_id=invoice_id)
            _refuse_ignored(op, clinic_id=clinic_id, limit=limit, offset=offset,
                            fields=fields, invoiced_from=invoiced_from,
                            invoiced_to=invoiced_to, max_pages=max_pages)
            return _one(_run(lambda: c.get_invoice(invoice_id)), "invoice", _invoice)
        raise _bad("op must be 'list', 'get', 'update', 'validate', 'pay' or "
                   "'credit_note'.")


    @mcp.tool()
    def nextmotion_payment(
        op: Literal["list", "get", "update"] = "list",
        clinic_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Payments of a Nextmotion clinic — amount per means (check, cash, card,
        transfer, stripe, voucher, other, custom mediums), deferred date, and the
        invoice paid. Health data, titles and free text are withheld; the patient is
        served by ID ONLY — resolve it with `nextmotion_patient(op="get")` when needed.

        `op`: "list" (default, `clinic_id`, optional `invoice_id`) | "get" | "update"
        (`payment_id` + `data`). A payment is recorded by
        `nextmotion_invoice(op="pay")` and never deleted. `data` takes the fields the
        Nextmotion spec accepts for the op; any other field is refused.
        ⚠️ `dry_run` DEFAULTS TO TRUE on update.

        Args:
            op: list (default) | get | update.
            clinic_id: op="list".
            payment_id: op="get"/"update".
            invoice_id: op="list" — only this invoice's payments.
            data: op="update" — the fields to send.
            dry_run: op="update" — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op == "update":
            return _serve_write(
                _PAYMENTS, "payment", op, client=_client, clinic_id=clinic_id,
                item_id=payment_id, data=data, dry_run=dry_run, item_name="payment_id",
                unused={"invoice_id": invoice_id, "limit": limit, "offset": offset,
                        "fields": fields})
        _refuse_ignored(op, data=data, dry_run=dry_run)
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, payment_id=payment_id)
            c = _client()
            return _page(_run(lambda: c.list_payments(
                clinic_id, invoice_id=invoice_id, **_paging(limit, offset))),
                "payments", _payment, fields=fields)
        if op == "get":
            _need(op, payment_id=payment_id)
            _refuse_ignored(op, clinic_id=clinic_id, invoice_id=invoice_id, limit=limit,
                            offset=offset, fields=fields)
            c = _client()
            return _one(_run(lambda: c.get_payment(payment_id)), "payment", _payment)
        raise _bad("op must be 'list', 'get' or 'update'.")

    @mcp.tool()
    def nextmotion_statistics(
        kind: Literal["appointment_income", "treatment_types", "treatment_types_income"],
        clinic_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period_type: Optional[Literal["year", "month", "week", "day"]] = None,
    ) -> dict:
        """Clinic-level statistics of a Nextmotion clinic, as bar charts: `title`,
        `labels` (one per period) and `datasets` (`data` aligned on the labels).

        `kind`:
        - "appointment_income" — income from appointments (one chart).
        - "treatment_types" — draft-quoted, quoted and invoiced treatment counts, one
          chart per treatment type.
        - "treatment_types_income" — income from invoiced treatments, one chart per
          treatment type.

        Args:
            kind: which statistic.
            clinic_id: the clinic.
            start_date / end_date: YYYY-MM-DD, end inclusive; omitted, Nextmotion
                spans 6 periods up to the current one.
            period_type: year | month (Nextmotion default) | week | day.
        """
        if kind not in _STATISTICS:
            raise _bad(f"kind inconnu : {kind!r}.")
        _need("statistics", clinic_id=clinic_id)
        c = _client()
        env = _run(lambda: _STATISTICS[kind](
            c, clinic_id, start_date=start_date, end_date=end_date,
            period_type=period_type))
        data = env.get("data") if isinstance(env, dict) else None
        charts = data if isinstance(data, list) else [data] if data is not None else []
        return {"kind": kind, "charts": [_chart(ch) for ch in charts]}

    @mcp.tool()
    def nextmotion_patient_stats(patient_id: str) -> dict:
        """Financial totals of ONE Nextmotion patient, by patient id: quoted,
        invoiced, paid, credit-note and reimbursement totals, first and last visit
        times, review requests and clicks. Nothing identifies the patient here; the
        identity is read with `nextmotion_patient(op="get")`.

        Args:
            patient_id: the patient id, as served by appointments, quotes, invoices,
                payments or journeys.
        """
        _need("get", patient_id=patient_id)
        c = _client()
        return _one(_run(lambda: c.get_patient_stats(patient_id)), "patient_stats",
                    _patient_stats, withheld=_WITHHELD)
