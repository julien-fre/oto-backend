"""Sellsy — CRM + FR sales management (api.sellsy.com/v2).

A single Sellsy account holds the customer relationship (companies, individuals, contacts,
opportunities) AND the sales chain (quote → order → invoice → credit note,
collections, catalog). The connector exposes both.

**Consolidated surface (ADR 0047)**: one tool per business OBJECT, the verb in `op`.
Two objects are carried by several API resources and therefore fit in a
single tool with `kind=`, because their PARAMETERS overlap exactly (the ADR's
merge criterion — not the count):
- `sellsy_document(kind=…)` — quote/order/invoice/credit note have the SAME parameters
  (same search filters, same `related`/`rows` body); separating them would have
  quadrupled an identical block while teaching the agent nothing.
- `sellsy_third_party(kind=…)` — company and individual are the two faces of the same
  role: the third party a document bills (`related` actually carries the
  discriminator, `{"id": 42, "type": "company"}`). Same verbs, same filters, same
  body; only `link_contact`/`unlink_contact` is specific to the company.

Named tools remain where the verbs do not factor out: `sellsy_contact`
(the person, not the third party: no `convert`, no collection), `sellsy_ref`
(read-only, neither `op` nor pagination) and `sellsy_search` (full text, `q` only).

Credential = multi-field OAuth2 client_credentials (client_id + client_secret,
created in Settings → Developer portal → API V2), resolved per call via
`access.resolve_credential_fields("sellsy")`. byo-only: each org connects ITS OWN
Sellsy account, there is no platform key to share.

Two guards are worth knowing before writing:
- **`op="create"` accepts `dry_run=True`** (the API's `verify` parameter): Sellsy
  validates the payload and persists nothing — the right reflex before a bulk
  creation, since required fields vary from one account to another (custom
  fields, numbering).
- **validating a document is irreversible**: `op="validate"` takes an invoice or
  a credit note out of draft state, gives it its final number and makes it
  accounting-relevant. To be called only after human validation.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# The two faces of the third party: agent name → API resource.
_THIRD_PARTIES = {
    "company": "companies",
    "individual": "individuals",
}

# The four sales documents: agent name → API resource.
_DOCUMENTS = {
    "estimate": "estimates",
    "invoice": "invoices",
    "order": "orders",
    "credit_note": "credit-notes",
}

# Reference data readable without writing — `sellsy_ref(kind=…)` → API path.
_REFS = {
    "staffs": "staffs",
    "custom_fields": "custom-fields",
    "pipelines": "opportunities/pipelines",
    "sources": "opportunities/sources",
    "categories": "opportunities/categories",
    "payment_methods": "payments/methods",
    "taxes": "taxes",
    "units": "units",
    "currencies": "currencies",
    "countries": "countries",
    "rate_categories": "rate-categories",
    "accounting_codes": "accounting-codes",
    "task_labels": "tasks/labels",
    "document_layouts": "document-layouts",
}

_COMMON_OPS = ("list", "search", "get", "create", "update", "delete",
               "custom_fields")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Sellsy rejected access (HTTP {status}) — check the connector's client_id / "
                "client_secret, and that the API V2 access carries the rights "
                f"(scopes) for this operation. {e.body}")
    if status == 402:
        return ("Sellsy: plan quota reached on this resource (402) — "
                f"creation is blocked on the subscription side. {e.body}")
    if status == 404:
        return f"Sellsy: object not found (404) — check the id. {e.body}"
    if status == 409:
        return f"Sellsy: conflict with the object's current state (409). {e.body}"
    if status == 429:
        return ("Sellsy: request quota exhausted (429) — quotas are counted "
                "per second/minute/day/month, retry later or reduce "
                "pagination (limit, all_pages).")
    if status in (500, 502, 503, 504):
        return f"Sellsy is temporarily unavailable (HTTP {status}) — retry later."
    return f"Sellsy refused the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: mints a token then reads ONE staff member —
    the cheapest authenticated call, with no side effect or required data."""
    from oto.tools.sellsy import SellsyClient
    SellsyClient(client_id=fields.get("client_id"),
                 client_secret=fields.get("client_secret")).list_records(
                     "staffs", limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.sellsy import SellsyClient

    connector_verify.register("sellsy", _verify)

    def _client() -> SellsyClient:
        creds = access.resolve_credential_fields("sellsy")
        return SellsyClient(client_id=creds.get("client_id"),
                            client_secret=creds.get("client_secret"))

    @contextmanager
    def _upstream():
        """Translates a Sellsy refusal into an actionable tool error."""
        try:
            yield
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, name: str, op: str):
        """Required argument for THIS op — actionable error, never a fallback."""
        if value is None:
            raise _bad(f"op='{op}' requires {name}")
        return value

    def _crud(c, resource: str, op: str, *, record_id=None, data=None,
              filters=None, limit=None, offset=None, order=None, direction=None,
              fields=None, embed=None, all_pages=False, max_pages=10,
              dry_run=None, extra_ops=()) -> Any:
        """The verbs that EVERY Sellsy resource exposes the same way.

        Returns None when `op` belongs to `extra_ops` (a verb specific to the
        calling tool, which then takes over); refuses any other `op`.
        """
        if op == "list":
            if all_pages:
                return c.list_all(resource, max_pages=max_pages,
                                  fields=fields, embed=embed)
            return c.list_records(resource, limit=limit, offset=offset, order=order,
                                  direction=direction, fields=fields, embed=embed)
        if op == "search":
            if all_pages:
                return c.list_all(resource, filters=filters or {},
                                  max_pages=max_pages, fields=fields, embed=embed)
            return c.search_records(resource, filters or {}, limit=limit,
                                    offset=offset, order=order, direction=direction,
                                    fields=fields, embed=embed)
        if op == "get":
            return c.get_record(resource, _need(record_id, "record_id", op),
                                fields=fields, embed=embed)
        if op == "create":
            return c.create_record(resource, _need(data, "data", op),
                                   embed=embed, verify=dry_run)
        if op == "update":
            return c.update_record(resource, _need(record_id, "record_id", op),
                                   _need(data, "data", op), embed=embed)
        if op == "delete":
            return c.delete_record(resource, _need(record_id, "record_id", op))
        if op == "custom_fields":
            record_id = _need(record_id, "record_id", op)
            if data is not None:
                return c.set_custom_fields(resource, record_id,
                                           data.get("custom_fields", []))
            return c.get_custom_fields(resource, record_id)
        if op not in extra_ops:
            raise _bad(f"Unknown op: {op!r} — expected: "
                       + ", ".join(_COMMON_OPS + tuple(extra_ops)))
        return None

    # --- CRM: third parties and contacts ------------------------------------

    @mcp.tool()
    def sellsy_third_party(
        kind: Literal["company", "individual"],
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "contacts", "convert", "link_contact", "unlink_contact",
                    "custom_fields", "record_payment"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        contact_id: Optional[int] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """CRM third parties: companies and individuals (clients, prospects, suppliers).

        `kind` ∈ "company" | "individual" — the two faces
        of the third party, same verbs and same parameters. The individual is the
        "natural person" counterpart of the company: a quote or an invoice attaches
        EITHER to a company, OR to an individual — this is where clients who are not
        businesses live (a document's `related` carries the same
        discriminator: `[{"id": 42, "type": "company"}]`).

        `op`:
        - "list" / "search": list and filtered list. Useful filters:
          `{"name": "acme"}`, `{"type": ["client"]}`, `{"created": {"start":
          "2026-01-01T00:00:00+01:00"}}`, `{"postal_code": ["13001"]}`; for
          individuals also `{"email": …}`.
        - "get" / "create" / "update" / "delete" (`record_id`, `data`).
          Creating requires `type` ∈ prospect | client | supplier, plus `name` for a
          company and `last_name` for an individual.
        - "contacts": the contacts attached to the third party.
        - "convert": turns a prospect into a client (irreversible on the Sellsy side).
        - "link_contact" / "unlink_contact" (`contact_id`): attaches or detaches
          an existing contact. **kind="company" only** — a contact
          attaches to a company, not to an individual.
        - "custom_fields": reads the custom fields; with
          `data={"custom_fields": [{"id": 12, "value": "x"}]}`, writes them.
        - "record_payment" (`data`): collection on the third party's account
          (`{"amount": {"value": "120.00", "currency": "EUR"}, "paid_at": …,
          "payment_method_id": …, "type": "credit"}`).

        Args:
            kind: the third-party type (above). op: the verb.
            record_id: third-party id (company or individual).
            data: write body. filters: op="search" filters.
            contact_id: op link_contact / unlink_contact (kind="company").
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection (`["id", "name"]`). embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        resource = _THIRD_PARTIES.get(kind)
        if resource is None:
            raise _bad(f"kind must be one of {', '.join(_THIRD_PARTIES)}")
        extra = ("contacts", "convert", "link_contact", "unlink_contact",
                 "record_payment")
        c = _client()
        with _upstream():
            out = _crud(c, resource, op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=extra)
            if out is not None:
                return out
            if op == "contacts":
                return c.list_sub(resource, _need(record_id, "record_id", op),
                                  "contacts", limit=limit, offset=offset)
            if op == "convert":
                return c.act(resource, _need(record_id, "record_id", op),
                             "convert", payload=data or {"target": "client"})
            if op in ("link_contact", "unlink_contact"):
                if kind != "company":
                    raise _bad(f"op='{op}' only applies to kind='company' — a "
                               "contact attaches to a company, not to an "
                               "individual")
                company_id = _need(record_id, "record_id", op)
                contact_id = _need(contact_id, "contact_id", op)
                if op == "link_contact":
                    return c.link_contact_to_company(company_id, contact_id,
                                                     payload=data)
                return c.unlink_contact_from_company(company_id, contact_id)
            return c.act(resource, _need(record_id, "record_id", op),
                         "payments", payload=_need(data, "data", op))

    @mcp.tool()
    def sellsy_contact(
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "companies", "custom_fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Contacts — the people attached to companies and individuals.

        A contact exists independently of the third party: attachment is done by
        `sellsy_third_party(kind="company", op="link_contact")`.

        `op`: "list" / "search" (filters `last_name`, `email`, `phone_number`,
        `companies`, `is_linked`…), "get" / "create" / "update" / "delete",
        "companies" (the contact's companies), "custom_fields".

        Args:
            op: the verb (above). record_id: contact id.
            data: write body. filters: op="search" filters.
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "contacts", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("companies",))
            if out is not None:
                return out
            return c.list_sub("contacts", _need(record_id, "record_id", op),
                              "companies", limit=limit, offset=offset)

    @mcp.tool()
    def sellsy_opportunity(
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "move", "custom_fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        step: Optional[int] = None, before_sibling: Optional[int] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Opportunities — the sales pipeline.

        `op`:
        - "list" / "search": filters `{"pipeline": [id]}`, `{"step": [id]}`,
          `{"statuses": ["open"]}`, `{"due_date": {"start": …, "end": …}}`,
          `{"assigned_staffs": [id]}`, `{"amount": {"min": …, "max": …}}`.
        - "get" / "create" / "update" / "delete". Creating requires `name`,
          `pipeline`, `step` and the third party in `related`
          (`[{"id": 42, "type": "company"}]`) — pipeline and step ids are
          read with `sellsy_ref(kind="pipelines" | "steps")`.
        - "move" (`step`, option `before_sibling`): moves the opportunity within the
          pipeline — this is the dedicated endpoint, `op="update"` does not change the step.
        - "custom_fields".

        Args:
            op: the verb (above). record_id: opportunity id.
            data: write body. filters: op="search" filters.
            step: op="move" — id of the destination step.
            before_sibling: op="move" — placed before this opportunity (otherwise at the
                last rank of the step).
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "opportunities", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("move",))
            if out is not None:
                return out
            payload = {"step": _need(step, "step", op)}
            if before_sibling is not None:
                payload["before_sibling"] = before_sibling
            return c.act("opportunities", _need(record_id, "record_id", op),
                         "step-rank", payload=payload, method="PATCH")

    # --- sales chain ---------------------------------------------------------

    @mcp.tool()
    def sellsy_document(
        kind: Literal["estimate", "order", "invoice", "credit_note"],
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "validate", "status", "payments", "linked",
                    "custom_fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Sales documents: quotes, orders, invoices, credit notes.

        `kind` ∈ "estimate" | "order" | "invoice" | "credit_note" — same verbs
        and same parameters for all four.

        `op`:
        - "list" / "search": filters `{"status": ["due"]}`, `{"number": "F-2026"}`,
          `{"date": {"start": "2026-01-01", "end": "2026-01-31"}}`,
          `{"related_objects": [{"id": 42, "type": "company"}]}`,
          `{"owners": [id]}`, `{"currency": ["EUR"]}`.
        - "get" / "create" / "update" / "delete" (`record_id`, `data`). A created
          document is a DRAFT. Minimal body: `related` (the third party,
          `[{"id": 42, "type": "company"}]`, exactly one company OR one
          individual), `date`, `subject`, `currency`, and `rows` — each row
          carries its `type`: `single` (free-form: `quantity`, `unit_amount`,
          `tax_id`), `catalog` (catalog item: `related` + `quantity`),
          `title`, `comment`, `sub-total`, `break-line`.
        - "validate" (invoice, credit note): **irreversible** — takes the document out of
          draft, freezes its number and makes it accounting-relevant. `data` can carry the
          validation `date`.
        - "status" (quote, `status`): draft, sent, read, accepted, refused,
          expired, cancelled.
        - "payments": the collections attached to the document.
        - "linked": the credit notes of an invoice, or the invoices of a credit note.
        - "custom_fields".

        Args:
            kind: the document type (above). op: the verb.
            record_id: document id. data: write body.
            filters: op="search" filters. status: op="status" — new status.
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        resource = _DOCUMENTS.get(kind)
        if resource is None:
            raise _bad(f"kind must be one of {', '.join(_DOCUMENTS)}")
        c = _client()
        with _upstream():
            out = _crud(c, resource, op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("validate", "status", "payments", "linked"))
            if out is not None:
                return out
            record_id = _need(record_id, "record_id", op)
            if op == "validate":
                if kind not in ("invoice", "credit_note"):
                    raise _bad("op='validate' only applies to kind='invoice' "
                               "or 'credit_note' (a quote changes state through "
                               "op='status')")
                return c.act(resource, record_id, "validate", payload=data or {})
            if op == "status":
                if kind != "estimate":
                    raise _bad("op='status' only applies to kind='estimate'")
                return c.act(resource, record_id, "status",
                             payload={"status": _need(status, "status", op)},
                             method="PUT")
            if op == "payments":
                return c.list_sub(resource, record_id, "payments", limit=limit,
                                  offset=offset)
            sub = {"invoice": "credit-notes", "credit_note": "invoices"}.get(kind)
            if sub is None:
                raise _bad("op='linked' only applies to kind='invoice' "
                           "(its credit notes) or 'credit_note' (its invoices)")
            return c.list_sub(resource, record_id, sub, limit=limit, offset=offset)

    @mcp.tool()
    def sellsy_payment(
        op: Literal["list", "search", "get", "delete",
                    "custom_fields"] = "list",
        record_id: Optional[int] = None,
        filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
    ) -> Any:
        """Collections — what has been paid, and against which document.

        `op`: "list" / "search" (filters `{"status": [...]}`,
        `{"related_objects": [{"id": 9, "type": "invoice"}]}`), "get", "delete",
        "custom_fields" (read-only here: this tool has no `data`).

        Recording a payment is done on the third party:
        `sellsy_third_party(op="record_payment")`, kind="company" or "individual".

        Args:
            op: the verb (above). record_id: payment id.
            filters: op="search" filters.
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
        """
        c = _client()
        with _upstream():
            return _crud(c, "payments", op, record_id=record_id, filters=filters,
                         limit=limit, offset=offset, order=order,
                         direction=direction, fields=fields, embed=embed,
                         all_pages=all_pages, max_pages=max_pages)

    @mcp.tool()
    def sellsy_item(
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "prices", "custom_fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Catalog — products, services, delivery fees.

        This is where to read the item ids to put in a document's
        `type="catalog"` row.

        `op`: "list" / "search" (filters `{"name": …}`, `{"reference": …}`,
        `{"type": ["product", "service"]}`, `{"is_archived": false}`),
        "get" / "create" / "update" / "delete" (creating requires `type` and
        `reference`), "prices" (the item's price grid),
        "custom_fields".

        Args:
            op: the verb (above). record_id: item id.
            data: write body. filters: op="search" filters.
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "items", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("prices",))
            if out is not None:
                return out
            return c.list_sub("items", _need(record_id, "record_id", op), "prices",
                              limit=limit, offset=offset)

    # --- follow-up -----------------------------------------------------------

    @mcp.tool()
    def sellsy_task(
        op: Literal["list", "search", "get", "create", "update", "delete",
                    "custom_fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Tasks — the follow-ups and actions attached to a third party or a document.

        `op`: "list" / "search" (filters `{"assigned_staffs": [id]}`,
        `{"due_date": {"start": …, "end": …}}`, `{"statuses": ["todo"]}`,
        `{"companies": [id]}`), "get" / "create" / "update" / "delete",
        "custom_fields".

        Creating requires `related` (`[{"id": 42, "type": "company"}]`); the usual
        fields are `title`, `due_date`, `assigned_staff_ids`, `priority`.

        Args:
            op: the verb (above). record_id: task id.
            data: write body. filters: op="search" filters.
            limit: page size (max 100). offset: `pagination.offset` cursor
                returned by the previous page. order / direction: sort (asc | desc).
            fields: projection. embed: related objects to include.
            all_pages / max_pages: walks the pagination (1 request per page).
            dry_run: op="create" — validates the payload WITHOUT persisting anything.
        """
        c = _client()
        with _upstream():
            return _crud(c, "tasks", op, record_id=record_id, data=data,
                         filters=filters, limit=limit, offset=offset, order=order,
                         direction=direction, fields=fields, embed=embed,
                         all_pages=all_pages, max_pages=max_pages, dry_run=dry_run)

    # --- reference data & cross-object search --------------------------------

    @mcp.tool()
    def sellsy_ref(kind: Literal["staffs", "pipelines", "steps", "sources",
                                 "categories", "custom_fields", "taxes", "units",
                                 "currencies", "countries", "payment_methods",
                                 "rate_categories", "accounting_codes",
                                 "task_labels", "document_layouts",
                                 "smart_tags"],
                   pipeline_id: Optional[int] = None,
                   linked_type: Optional[str] = None,
                   limit: Optional[int] = None) -> Any:
        """Account reference data, READ-ONLY — the ids to resolve BEFORE
        writing (never guess a step, tax or staff id).

        `kind`:
        - "staffs": staff members (owner_id, assigned_staff_ids).
        - "pipelines": opportunity pipelines; "steps" (`pipeline_id`):
          their steps; "sources" / "categories": origin of opportunities.
        - "custom_fields": the account's custom fields (id + type + code).
        - "taxes": VAT rates (a row's `tax_id`); "units": units;
          "currencies"; "countries"; "payment_methods"; "rate_categories";
          "accounting_codes"; "task_labels"; "document_layouts".
        - "smart_tags" (`linked_type`): existing tags for an object
          type (company, individual, contact, opportunity, invoice…).

        Args:
            kind: the wanted reference data.
            pipeline_id: kind="steps" — the steps of THIS pipeline.
            linked_type: kind="smart_tags" — the carrying object type.
            limit: page size.
        """
        c = _client()
        with _upstream():
            if kind == "smart_tags":
                return c.smart_tags_autocomplete(
                    _need(linked_type, "linked_type", "smart_tags"))
            if kind == "steps":
                pipeline_id = _need(pipeline_id, "pipeline_id", "steps")
                return c.list_records(
                    f"opportunities/pipelines/{pipeline_id}/steps", limit=limit)
            path = _REFS.get(kind)
            if path is None:
                raise _bad("kind must be one of "
                           + ", ".join(list(_REFS) + ["steps", "smart_tags"]))
            return c.list_records(path, limit=limit)

    @mcp.tool()
    def sellsy_search(q: str, types: Optional[list] = None,
                      limit: Optional[int] = None,
                      archived: Optional[bool] = None) -> Any:
        """Cross-object full-text search — find an object when you do not know
        which table it lives in ("who is Acme for us?").

        To filter finely (dates, statuses, amounts), go through the
        `op="search"` of the relevant object: this one only does full text.

        Args:
            q: the searched text (name, email, document number…).
            types: restricts to the wanted types — `company`, `company.client`,
                `company.prospect`, `individual`, `contact`, `opportunity`,
                `item`, `purchase`…
            limit: number of results (max 100).
            archived: include archived objects.
        """
        with _upstream():
            return _client().global_search(q, types=types, limit=limit,
                                           archived=archived)
