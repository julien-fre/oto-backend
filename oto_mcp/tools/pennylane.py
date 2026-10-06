"""Pennylane — accounting (read + supervised invoice/credit-note flow).

Key resolved per call via `access.resolve_api_key("pennylane")`: per-user
key model (like Attio), no platform key. Each user sets
their own Pennylane key on `manage.oto.cx/api-keys` — their accounting is
visible only to them.

**Consolidated surface (ADR 0047 applied to a connector)**: one tool per business
OBJECT, the verb as an `op` parameter — `pennylane_customer`, `pennylane_invoice`
(sales), `pennylane_supplier` (purchases; supplier invoices live
in `pennylane_achats.py`, `pennylane_supplier_invoice`), plus a single
`pennylane_ref` for read-only reference data (company, fiscal years,
chart of accounts, categories, invoice templates, products). The heterogeneous verbs that
merging would not factor out stay named: `pennylane_transactions`,
`pennylane_trial_balance`, `pennylane_match`, `pennylane_upload_file`.

Committing writes are **draft-first**: creating an invoice or a
credit note produces a draft, and **finalizing/sending are separate `op`s** that
the agent only calls after human validation (supervision model validated with
a customer). Lettering (`pennylane_match`) remains exposed: a reversible
reconciliation link, not an entry.

**Purchasing** is covered to file a supplier invoice from a file
"on the oto side": `pennylane_upload_file` (posts a PDF designated by its oto source —
Drive/Gmail/URL) then `pennylane_supplier_invoice(op="import")` (draft,
fields supplied by the agent that read the PDF).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify

from .. import file_source
from .pennylane_socle import _bad, _client, _ecrit, _need


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` + PERMISSIONS.

    `GET /api/external/v2/me`. What Pennylane's docs establish, quoted:

    - **authenticated** — "Authentication Required: OAuth2", and a documented 401
      "Access token is missing or invalid";
    - **no side effect** — a GET that "Returns the user and the company";
      the docs mention none;
    - **the cost** — "Cost/Billing/Quotas: No information provided". ⚠️ As with
      Folk, this is NOT a line saying "free": it is the absence of any credit
      counter in the documentation. A strong argument, not proof.

    **Authenticated ≠ usable** (class named on oto#69, with attio): this
    probe goes further than authentication, and that is deliberate. The response
    carries `scopes` — the exact list of the key's permissions. A Pennylane key
    can perfectly well authenticate and be able to do nothing: the model is one key
    per person or per team, each with its own scope, and Pennylane split
    its scopes (`journals:*`, `ledger_accounts:*`, `ledger_entries:*`…). Returning
    "connected" on a key with zero permissions would be the hollow verdict the probe
    exists to prevent — same lesson as the Zoho probe (auth OK, zero CRM scope)
    and Stripe's (key restricted to Balance:read only).

    Does NOT read a quota: Pennylane exposes none on this call.
    """
    from oto.tools.pennylane import PennylaneClient

    infos = PennylaneClient(api_key=fields["key"]).get_company_info()
    if not (infos or {}).get("company"):
        raise RuntimeError(
            "Pennylane answered without naming a company for this key — "
            f"unexpected response: {str(infos)[:200]}")
    scopes = infos.get("scopes")
    if isinstance(scopes, list) and not scopes:
        raise RuntimeError(
            "The key does authenticate (company "
            f"« {(infos.get('company') or {}).get('name') or '?'} » recognized) but carries "
            "NO permission: it will not be able to read or write anything. "
            "Regenerate it at Pennylane, ticking the desired scopes.")


def register(mcp: FastMCP) -> None:
    connector_verify.register("pennylane", _verify)
    # --- reference data (read-only) -----------------------------------------

    @mcp.tool()
    def pennylane_ref(kind: Literal["company", "fiscal_years", "ledger_accounts",
                                    "journals", "categories", "invoice_templates",
                                    "products"],
                      product_id: Optional[int] = None,
                      max_pages: Optional[int] = None) -> dict | list:
        """Pennylane reference data, READ-ONLY — what must be resolved BEFORE
        writing (product ids, invoice templates, accounts, categories).

        `kind`:
        - "company": information about the current account's company.
        - "fiscal_years": fiscal years.
        - "ledger_accounts": chart of accounts (general ledger accounts).
        - "journals": journals (`{id, code, label, type}` — "HA" purchases, "VT"
          sales, "BQ" bank…). Prerequisite of a general-ledger entry, which
          requires a `journal_id`: these ids are SPECIFIC TO THE COMPANY, resolve them
          here rather than hard-coding them. Scope `journals:*`, distinct from that
          of entries — a key can read one without the other.
        - "categories": expense categories.
        - "invoice_templates": invoicing templates. The template drives the PDF
          RENDERING — notably the "Avoir" (credit note) template (without payment/IBAN block), the only API
          way to get the credit-note rendering: pass its id as
          `customer_invoice_template_id` to `pennylane_invoice`. Required scope:
          customer_invoice_templates:readonly.
        - "products": product catalog (id, label, price, unit, vat_rate…).
          With `product_id` → the record of ONE product. Used to resolve the
          `product_id` of an invoice or credit-note line — never guess a
          product_id: read it here. Beware of near-homonymous labels (e.g.
          two "credit …" products): choose on the full record (price,
          unit), not on the start of the name.

        Args:
            kind: the desired reference data (see above).
            product_id: kind="products" — the record of a single product.
            max_pages: pagination limit (paginated kinds: products,
                invoice_templates).
        """
        c = _client()
        if kind == "company":
            return c.get_company_info()
        if kind == "fiscal_years":
            return c.get_fiscal_years()
        if kind == "ledger_accounts":
            return c.get_ledger_accounts()
        if kind == "journals":
            return c.get_journals(max_pages=max_pages)
        if kind == "categories":
            return c.get_categories()
        if kind == "invoice_templates":
            return c.list_invoice_templates(max_pages=max_pages)
        if kind == "products":
            if product_id is not None:
                return c.get_product(product_id)
            return c.list_products(max_pages=max_pages)
        raise _bad("kind must be 'company', 'fiscal_years', 'ledger_accounts', "
                   "'journals', 'categories', 'invoice_templates' or 'products'")

    # --- customers -----------------------------------------------------------

    @mcp.tool()
    def pennylane_customer(
        op: Literal["list", "find", "create", "update"] = "list",
        customer_id: Optional[int] = None,
        external_reference: Optional[str] = None,
        name: Optional[str] = None,
        address: Optional[str] = None,
        postal_code: Optional[str] = None,
        city: Optional[str] = None,
        country_alpha2: str = "FR",
        emails: Optional[list] = None,
        fields: Optional[dict] = None,
        max_pages: Optional[int] = None,
    ) -> dict | list:
        """Pennylane customers (companies) — list, find, create, complete.

        `op`:
        - "list": id + name + contact details. Used to resolve a `customer_id` from
          a NAME (`pennylane_invoice(op="list")` only returns `customer.{id,url}`,
          with no name or filter). Paginated (`max_pages`).
        - "find" (`external_reference`): native server-side filter, a single call.
          **Anti-duplicate**: an already created customer (e.g. back-office companyId as
          external_reference) makes `op="create"` fail with 422 "External reference
          has already been taken". Call BEFORE creating: if it exists,
          reuse its `id`. Returns the customer or `{"found": false}`.
        - "create": the full billing address is MANDATORY (API v2) —
          `name`, `address`, `postal_code`, `city`, `country_alpha2`. Returns the
          created customer with its `id` (to pass as `customer_id` of
          `pennylane_invoice(op="create")`).
        - "update" (`customer_id`, `fields`): complete email, vat_number,
          reg_no, billing_iban, external_reference… Typical case: Pennylane "knows"
          an imported customer but with no email or identifier — complete before
          creating the credit note.

        Args:
            op: "list" (default) | "find" | "create" | "update".
            customer_id: required for op="update".
            external_reference: op="find" (search) or op="create" (source
                trace / anti-duplicate).
            name / address / postal_code / city / country_alpha2: op="create"
                (country_alpha2 = ISO alpha-2, default FR).
            emails: op="create" — invoice recipients.
            fields: op="update" — fields to update, e.g.
                {"emails": ["x@y.fr"], "external_reference": "MM-12345"}.
            max_pages: op="list" — pagination limit.
        """
        c = _client()
        if op == "list":
            return c.list_customers(max_pages=max_pages)
        if op == "find":
            found = c.find_customer_by_external_reference(
                _need(external_reference, "external_reference", op))
            return found if found else {"found": False}
        if op == "create":
            return _ecrit(lambda: c.create_customer(
                name=_need(name, "name", op), emails=emails,
                address=_need(address, "address", op),
                postal_code=_need(postal_code, "postal_code", op),
                city=_need(city, "city", op), country_alpha2=country_alpha2,
                external_reference=external_reference), "customer creation")
        if op == "update":
            return _ecrit(lambda: c.update_customer(_need(customer_id, "customer_id", op),
                                            **(fields or {})),
                          "customer update")
        raise _bad("op must be 'list', 'find', 'create' or 'update'")

    # --- SALES invoices + credit notes (draft-first, supervision) ------------

    @mcp.tool()
    def pennylane_invoice(
        op: Literal["list", "find", "create", "credit_note", "update",
                    "finalize", "send", "delete"] = "list",
        invoice_id: Optional[int] = None,
        customer_id: Optional[int] = None,
        date: Optional[str] = None,
        deadline: Optional[str] = None,
        lines: Optional[list] = None,
        external_reference: Optional[str] = None,
        free_text: Optional[str] = None,
        customer_invoice_template_id: Optional[int] = None,
        credited_invoice_id: Optional[int] = None,
        fields: Optional[dict] = None,
        max_pages: Optional[int] = None,
    ) -> dict | list:
        """SALES invoices (customer invoices) and CREDIT NOTES — the "revenue" side.

        `op`:
        - "list": the inventory of issued invoices, to be checked against receipts
          for a bank reconciliation (receipt ↔ customer invoice direction) or
          to spot unpaid invoices / the remaining balance due. ⚠️ Without `max_pages`, the WHOLE
          history comes back (may exceed the token limit) — start small
          then widen. Does NOT look for a specific invoice: see op="find".
        - "find" (`external_reference`): anti-duplicate before creating a credit note for
          a failed payment — if an invoice already carries this reference (e.g. the
          GoCardless id `PM…`), the credit note exists, do not recreate it. Search by server-side
          filter: EXHAUSTIVE (an invoice's age or archiving does not
          hide it) and reliable — an upstream outage raises instead of reading as
          "none". Returns the invoice or `{"found": false}`.
        - "create": sales invoice as a **draft**. The customer must exist
          (`pennylane_customer`).
        - "credit_note": **standalone** credit note as a draft (v2 convention:
          negative amounts). Provide the lines as **POSITIVE** (the business action:
          195 credits at 1.45) — the negation that makes it a CREDIT NOTE is applied
          server-side, never by you. **No linked invoice by default** (the
          reference lives in free text); if `credited_invoice_id` is provided, the
          link is set AFTER creation via the dedicated endpoint (the create-time
          field is broken on Pennylane's side).
        - "update" (`invoice_id`, `fields`): on a DRAFT (date, deadline,
          customer_invoice_template_id, pdf_invoice_free_text, external_reference…).
          On a FINALIZED document the API only allows label /
          transaction_reference / external_reference — do not use it to
          "correct" an issued document. Typical use: switch a draft credit note
          to the "Avoir" template before finalization.
        - "finalize" (`invoice_id`): gives the draft its definitive reference.
          ⚠️ Committing write — after explicit human validation ONLY.
        - "send" (`invoice_id`): sends the finalized document to the customer by email
          (the customer's Pennylane email). ⚠️ External send — after explicit human
          validation ONLY.
        - "delete" (`invoice_id`): deletes a DRAFT — cleanup of an obsolete draft
          (replaced, never finalized) before it pollutes the view or is
          finalized by mistake. Pennylane refuses an already FINALIZED document (it must be
          cancelled by credit note instead) — the refusal is surfaced as is, never a
          silent fallback.

        ⚠️ Strict LINE schema for create/credit_note (any deviation → opaque 400
        `NotAnyOf`) — a line = ONE of 2 forms, no field outside the list:
        - product: `{product_id: int, quantity: number}` (the product fills the
          rest; possible overrides: label, raw_currency_unit_price, unit,
          vat_rate) — resolve the product_id via `pennylane_ref(kind="products")`;
        - free: `{label: str, quantity: number, unit: str,
          raw_currency_unit_price: str, vat_rate: str}` — ALL required.
        `vat_rate` = Pennylane code, never a percentage: 20 %→"FR_200",
        10 %→"FR_100", 5.5 %→"FR_55", 2.1 %→"FR_21", exempt→"exempt".
        `raw_currency_unit_price` = unit price excl. VAT as a STRING ("700.00");
        `quantity` = number (never a string).

        Args:
            op: "list" (default) | "find" | "create" | "credit_note" | "update" |
                "finalize" | "send" | "delete".
            invoice_id: required for update / finalize / send / delete.
            customer_id: required for create / credit_note.
            date: issue date of the invoice / credit note (YYYY-MM-DD).
            deadline: due date (YYYY-MM-DD).
            lines: lines in the strict schema above (create / credit_note).
            external_reference: op="find" (search); create/credit_note (source
                trace, e.g. GoCardless payment id — anti-duplicate).
            free_text: free text printed on the PDF (API field
                `pdf_invoice_free_text`) — THAT is where the readable link
                to the original invoice of a credit note not structurally linked lives (e.g.
                "Credit note on invoice AUT-XXXXX following failed direct debit").
            customer_invoice_template_id: PDF rendering template
                (`pennylane_ref(kind="invoice_templates")`).
            credited_invoice_id: op="credit_note" — invoice to credit; the link is
                set after creation (2nd call), never at create.
            fields: op="update" — fields to update.
            max_pages: op="list" — bounds the pagination (⚠️ see above).
        """
        c = _client()
        if op == "list":
            return c.get_customer_invoices(max_pages=max_pages)
        if op == "find":
            inv = c.find_invoice_by_external_reference(
                _need(external_reference, "external_reference", op))
            return inv if inv else {"found": False}
        if op in ("create", "credit_note"):
            kwargs = dict(
                customer_id=_need(customer_id, "customer_id", op),
                date=_need(date, "date", op),
                deadline=_need(deadline, "deadline", op),
                lines=_need(lines, "lines", op),
                external_reference=external_reference, pdf_free_text=free_text,
                customer_invoice_template_id=customer_invoice_template_id,
                draft=True)
            if op == "create":
                return _ecrit(lambda: c.create_customer_invoice(**kwargs), "invoice creation")
            note = _ecrit(lambda: c.create_credit_note(**kwargs), "credit note creation")
            if credited_invoice_id:
                note_id = note.get("id") or (note.get("customer_invoice") or {}).get("id")
                if not note_id:
                    return {"credit_note": note,
                            "link": "NOT set: credit note id not found in the response"}
                return {"credit_note": note,
                        "link": _ecrit(lambda: c.link_credit_note(credited_invoice_id, note_id),
                                       "linking the credit note to its invoice")}
            return note
        if op == "update":
            return _ecrit(lambda: c.update_invoice(_need(invoice_id, "invoice_id", op),
                                           **(fields or {})), "invoice update")
        if op == "finalize":
            return _ecrit(lambda: c.finalize_invoice(_need(invoice_id, "invoice_id", op)),
                          "invoice finalization")
        if op == "send":
            return _ecrit(lambda: c.send_invoice(_need(invoice_id, "invoice_id", op)),
                          "invoice sending")
        if op == "delete":
            return _ecrit(lambda: c.delete_invoice(_need(invoice_id, "invoice_id", op)),
                          "invoice deletion")
        raise _bad("op must be 'list', 'find', 'create', 'credit_note', 'update', "
                   "'finalize', 'send' or 'delete'")

    # --- purchases: suppliers + supplier invoices ----------------------------

    @mcp.tool()
    def pennylane_supplier(op: Literal["list", "create"] = "list",
                           name: Optional[str] = None,
                           fields: Optional[dict] = None,
                           max_pages: Optional[int] = None) -> dict | list:
        """Pennylane suppliers.

        `op`:
        - "list": id + name. Used to find the `supplier_id` of an EXISTING
          supplier to reuse in `pennylane_supplier_invoice(op="import")` (which
          requires a supplier_id). Paginated.
        - "create": do this before entering the supplier invoice of a NEW
          supplier. Returns the created supplier (with its id).

        Args:
            op: "list" (default) | "create".
            name: op="create" — company name (mandatory).
            fields: op="create" — other optional Pennylane fields (e.g.
                {"vat_number": "FR…", "reg_no": "123456789",
                "emails": ["compta@exemple.fr"]}).
            max_pages: op="list" — pagination limit.
        """
        c = _client()
        if op == "list":
            return c.list_suppliers(max_pages=max_pages)
        if op == "create":
            return _ecrit(lambda: c.create_supplier(_need(name, "name", op), **(fields or {})),
                          "supplier creation")
        raise _bad("op must be 'list' or 'create'")

    @mcp.tool()
    def pennylane_upload_file(source: dict, account: Optional[str] = None) -> dict:
        """Upload a file (PDF) to Pennylane from a file that lives "oto-side".

        The agent has no local disk: designate the file by a `source` reference
        that oto resolves to bytes server-side, then uploads to Pennylane.
        `source` (object, `kind` selects the origin):
        - Drive: `{"kind":"drive","file_id":"<id>"}` (id from drive_file op=list/metadata)
        - Gmail attachment: `{"kind":"gmail","message_id":"<id>","filename":"<name>"}`
        - URL: `{"kind":"url","url":"https://…"}` (e.g. a signed URL from
          drive_file op="download" / gmail_message op="attachment")
        - Project file: `{"kind":"project_file","project_id":<id>,"file_id":<id>}`
          (ids from oto_project_files op=list)
        Optional `account` (email) targets a specific Google account for drive/gmail.

        Returns {file_attachment_id, filename, url}. Feed `file_attachment_id` to
        `pennylane_supplier_invoice(op="import")` to create the supplier invoice.
        """
        try:
            rf = file_source.resolve(source)
        except file_source.FileSourceError as e:
            raise _bad(str(e))
        res = _ecrit(lambda:
            _client().upload_file_bytes(rf.data, rf.filename, rf.mime or "application/pdf"),
            "file upload")
        if not res.get("id"):
            raise _bad(f"Pennylane accepted the file upload but returned no "
                       f"`id` — nothing to attach to an invoice. Response: {str(res)[:300]}")
        return {"file_attachment_id": res["id"], "filename": rf.filename, "url": res.get("url")}

    # --- bank & lettering ----------------------------------------------------

    @mcp.tool()
    def pennylane_transactions(max_pages: Optional[int] = None,
                               period_start: Optional[str] = None,
                               period_end: Optional[str] = None,
                               only_outstanding: bool = False,
                               per_page: int = 100) -> list:
        """Bank transactions. ⚠️ Without a lever, the WHOLE history comes back
        (hundreds of transactions → exceeds the token limit): reduce
        the volume at the source with the optional filters.

        Args:
            max_pages: limits the number of pages fetched.
            period_start / period_end: date bounds YYYY-MM-DD (filtered on the
                Pennylane server side).
            only_outstanding: True → only unsettled transactions
                (outstanding_balance ≠ 0), e.g. for a bank reconciliation.
            per_page: page size (≤100) — refines the granularity of max_pages.
        """
        return _client().get_transactions(
            max_pages=max_pages, period_start=period_start, period_end=period_end,
            only_outstanding=only_outstanding, per_page=per_page)

    @mcp.tool()
    def pennylane_trial_balance(start_date: str, end_date: str) -> list:
        """Trial balance over a period.

        Args:
            start_date: start of period (YYYY-MM-DD).
            end_date: end of period (YYYY-MM-DD).
        """
        return _client().get_trial_balance(start_date, end_date)

    @mcp.tool()
    def pennylane_match(
        invoice_id: int,
        transaction_id: int,
        invoice_type: str = "customer",
    ) -> dict:
        """Reconciles a bank transaction with an invoice.

        A reversible reconciliation link, not an accounting entry. To
        be used to settle a paid invoice whose incoming transfer
        is not lettered (otherwise Pennylane keeps it `late` and chases the
        customer wrongly).

        ⚠️ **Do not confuse with general-ledger lettering.** The word
        "lettering" covers two actions on two objects: here a bank
        transaction and an invoice; associating entry LINES with each other in the
        general ledger is `pennylane_ledger_lettering`. Picking the wrong tool does not
        produce an error, only an action performed in the wrong place.

        Args:
            invoice_id: invoice ID (customer or supplier).
            transaction_id: bank transaction ID.
            invoice_type: "customer" (sales) or "supplier" (purchases).
        """
        return _ecrit(lambda: _client().match_transaction(invoice_id, transaction_id, invoice_type),
                      "bank/invoice reconciliation")
