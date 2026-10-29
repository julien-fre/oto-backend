"""Zoho Sign — electronic signature: signature requests, templates, signed documents.

Wraps `oto.tools.zohosign.client.ZohoSignClient` (API v1, `https://sign.zoho.<domain>`).
The fourth Zoho connector: same credential as the CRM and Desk (self-client fields, or
the server-based consent of `auth/zoho.py`, which fills `refresh_token`), same region
field, same two-step state (`tools/zoho._credential_state_for`).

**Two tools, the connector's pair** (`tools/lecture.py`):
- `zohosign` reads (`LECTURE`): requests, one request with its recipients, the signed
  PDF or the completion certificate, templates.
- `zohosign_act` acts (`ACTE`) in the account's name, toward the recipients: send a
  request from a template, remind, cancel (Zoho's "recall"). `dry_run=True` previews
  who would receive what, without calling Zoho's write. Nothing here deletes.

Files come back like every other connector's (`file_content.render_for_agent`): a PDF's
text inline plus a short-lived signed URL to the original, a ZIP as a signed URL.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, file_content, status_hints
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import ACTE, LECTURE

if TYPE_CHECKING:  # the client is imported lazily, at call time
    from oto.tools.zohosign import ZohoSignClient

CONNECTOR = "zohosign"

# Zoho Sign hosts per data center (reference, "API Root Endpoint": `sign.zoho.<domain>`,
# `accounts.zoho.<domain>`). The app, the refresh token and the account are bound to
# their region: a call to another one fails. The regions are those of `auth/zoho._ACCOUNTS`.
_DC_DOMAINS = {
    "com": ("https://sign.zoho.com", "https://accounts.zoho.com"),
    "eu": ("https://sign.zoho.eu", "https://accounts.zoho.eu"),
    "in": ("https://sign.zoho.in", "https://accounts.zoho.in"),
    "au": ("https://sign.zoho.com.au", "https://accounts.zoho.com.au"),
    "jp": ("https://sign.zoho.jp", "https://accounts.zoho.jp"),
    "ca": ("https://sign.zohocloud.ca", "https://accounts.zohocloud.ca"),
}

#: What a listing keeps of a request — enough to pick one; `full=True` gives Zoho's record.
_REQUEST_KEYS = ("request_id", "request_name", "request_status", "owner_email",
                 "created_time", "modified_time", "action_time", "expire_by",
                 "sign_percentage", "is_sequential")
_ACTION_KEYS = ("action_id", "action_type", "action_status", "recipient_name",
                "recipient_email", "signing_order")
_TEMPLATE_KEYS = ("template_id", "template_name", "owner_email", "created_time",
                  "modified_time", "is_sequential", "expiration_days")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _domains(data_center: Optional[str]) -> tuple[str, str]:
    """`(api_domain, accounts_url)` of the declared region — never a silent default."""
    dc = (data_center or "").strip().lower()
    if dc not in _DC_DOMAINS:
        raise _bad((f"Unrecognized Zoho data center: {data_center!r}." if dc
                    else "Zoho data center missing.")
                   + " Fill in the \"Data center\" field of the Zoho Sign connector — one of: "
                   + ", ".join(_DC_DOMAINS) + " (sign.zoho.eu → \"eu\").")
    return _DC_DOMAINS[dc]


def _build(fields: dict) -> ZohoSignClient:
    from oto.tools.zohosign import ZohoSignClient
    api_domain, accounts_url = _domains(fields.get("data_center"))
    return ZohoSignClient(client_id=fields.get("client_id"),
                          client_secret=fields.get("client_secret"),
                          refresh_token=fields.get("refresh_token"),
                          api_domain=api_domain, accounts_url=accounts_url)


def _client() -> ZohoSignClient:
    from .zoho import _credential_state_for
    fields = access.resolve_credential_fields(CONNECTOR)
    state = _credential_state_for(CONNECTOR)(fields)
    if not state.complete:
        raise _bad(state.next_action)
    return _build(fields)


def _run(call):
    """The upstream refusal, named — never a bare traceback; a client-side refusal of an
    argument (`ValueError`) is the agent's to fix."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.zoho import ZohoAuthError
    try:
        return call()
    except ZohoAuthError as e:
        raise _bad(str(e)) from None
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        if e.status_code == 404:
            raise _bad("Zoho Sign: not found (404) — check the id.") from None
        raise


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (probe contract)
    """"Test the connection": refresh, then ONE request of the list (`row_count=1`) — a
    read with no side effect, served at both access levels."""
    from .zoho import _zoho_error_hint
    status_hints.require_complete(CONNECTOR, fields)
    try:
        _build(fields).list_requests(row_count=1)
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 — the provider error IS the probe's return
        raise ValueError(_zoho_error_hint(e)) from None


def _slim_actions(actions: Any) -> list:
    return [{k: a[k] for k in _ACTION_KEYS if k in a} for a in actions or []
            if isinstance(a, dict)]


def _slim_request(r: dict) -> dict:
    out = {k: r[k] for k in _REQUEST_KEYS if k in r}
    out["recipients"] = _slim_actions(r.get("actions"))
    docs = r.get("document_ids") or []
    if docs:
        out["documents"] = [{k: d[k] for k in ("document_id", "document_name", "total_pages")
                             if k in d} for d in docs if isinstance(d, dict)]
    return out


def _slim_template(t: dict, detail: bool) -> dict:
    out = {k: t[k] for k in _TEMPLATE_KEYS if k in t}
    out["recipients"] = [{k: a[k] for k in ("action_id", "action_type", "role",
                                            "recipient_name", "recipient_email",
                                            "signing_order", "verify_recipient") if k in a}
                         for a in t.get("actions") or [] if isinstance(a, dict)]
    if detail:
        # The fields a request made from this template can prefill, by label and type.
        out["fields"] = [{k: f[k] for k in ("field_id", "field_label", "field_name",
                                            "field_type_name", "field_category",
                                            "is_mandatory", "default_value") if k in f}
                         for d in t.get("document_fields") or [] if isinstance(d, dict)
                         for f in d.get("fields") or [] if isinstance(f, dict)]
    return out


def _render_file(blob: dict) -> dict:
    sub = access.current_user_sub_or_raise()
    try:
        return file_content.render_for_agent(blob["content"], blob["filename"],
                                             blob["content_type"], sub=sub,
                                             prefix="zohosign-files")
    except (file_content.MediaUnavailable, file_content.SpreadsheetError) as e:
        raise _bad(str(e)) from None


def _hors_op(op: str, **unused) -> None:
    """An argument the op does not use is refused, never ignored."""
    given = sorted(k for k, v in unused.items() if v not in (None, False))
    if given:
        raise _bad(f"op={op!r} does not take {', '.join(given)}.")


def _need(op: str, **required) -> None:
    missing = sorted(k for k, v in required.items() if not v)
    if missing:
        raise _bad(f"op={op!r} requires {', '.join(missing)}.")


def register(mcp: FastMCP) -> None:
    connector_verify.register(CONNECTOR, _verify)

    @mcp.tool(annotations=LECTURE)
    def zohosign(
        op: Literal["list", "get", "download_pdf", "download_certificate",
                    "list_templates", "get_template"] = "list",
        request_id: Optional[str] = None,
        template_id: Optional[str] = None,
        start_index: int = 1,
        row_count: int = 25,
        sort_column: Optional[str] = None,
        sort_order: Optional[Literal["ASC", "DESC"]] = None,
        search: Optional[dict] = None,
        status: Optional[str] = None,
        with_certificate: Optional[bool] = None,
        merge: Optional[bool] = None,
        full: bool = False,
    ) -> dict:
        """Zoho Sign signature requests and templates — read only. Sending, reminding
        and recalling are `zohosign_act`.

        Args:
            op: which read.
                · "list" — one page of signature requests (light view: id, name,
                  status, recipients with their status). Paginate with `start_index`
                  / `row_count`; `page_context.has_more_rows` says if more remain.
                · "get" — one request (`request_id`): recipients, their status and
                  signing order, documents.
                · "download_pdf" — the request's signed documents (`request_id`): the
                  PDF text inline plus `raw_url`, a short-lived signed URL to the
                  original; a ZIP when it holds several documents and `merge` is not
                  set. `with_certificate=True` appends the completion certificate.
                · "download_certificate" — the completion certificate of a completed
                  request (`request_id`).
                · "list_templates" — one page of templates (`row_count` 10, 25, 50
                  or 100) with their recipient roles.
                · "get_template" — one template (`template_id`): its recipients
                  (`action_id`, role) and the fields a request can prefill — read it
                  before `zohosign_act(op="send_from_template")`.
            search: op="list" — Zoho filters, e.g. {"recipient_email": "a@b.c"};
                keys: request_name, folder_name, owner_full_name, recipient_email,
                recipient_name, form_name, template_name.
            status: op="list" — keep only the requests of this `request_status` (e.g.
                inprogress, completed, declined, expired, recalled, draft). ⚠️ Zoho
                documents no status filter: it applies to the PAGE returned, so page on.
            sort_column: op="list" — request_name, folder_name, owner_full_name,
                recipient_email, form_name or created_time (default).
            full: True = Zoho's raw records instead of the light view.
        """
        listing = op in ("list", "list_templates")
        if not listing:
            _hors_op(op, search=search, status=status, sort_column=sort_column,
                     sort_order=sort_order)
        if op not in ("download_pdf",):
            _hors_op(op, with_certificate=with_certificate, merge=merge)
        tri = {"sort_column": sort_column or "created_time",
               "sort_order": sort_order or "DESC"}

        if op == "list":
            _hors_op(op, request_id=request_id, template_id=template_id)
            page = _run(lambda: _client().list_requests(
                start_index=start_index, row_count=row_count, search_columns=search, **tri))
            rows = [r for r in page.get("requests") or [] if isinstance(r, dict)]
            if status:
                rows = [r for r in rows if str(r.get("request_status") or "").lower()
                        == status.strip().lower()]
            out = {"requests": rows if full else [_slim_request(r) for r in rows],
                   "page_context": page.get("page_context")}
            if status:
                out["note"] = (f"`status` filtered this page only ({len(rows)} kept); "
                               "follow page_context.has_more_rows for the rest.")
            return out

        if op == "list_templates":
            _hors_op(op, request_id=request_id, template_id=template_id, status=status)
            page = _run(lambda: _client().list_templates(
                start_index=start_index, row_count=row_count, search_columns=search, **tri))
            rows = [t for t in page.get("templates") or [] if isinstance(t, dict)]
            return {"templates": rows if full else [_slim_template(t, False) for t in rows],
                    "page_context": page.get("page_context")}

        if op == "get_template":
            _need(op, template_id=template_id)
            _hors_op(op, request_id=request_id)
            tpl = (_run(lambda: _client().get_template(template_id)) or {}).get("templates") or {}
            return {"template": tpl if full else _slim_template(tpl, True)}

        _need(op, request_id=request_id)
        _hors_op(op, template_id=template_id)
        if op == "get":
            req = (_run(lambda: _client().get_request(request_id)) or {}).get("requests") or {}
            return {"request": req if full else _slim_request(req)}
        _hors_op(op, full=full)
        if op == "download_pdf":
            return _render_file(_run(lambda: _client().download_pdf(
                request_id, with_coc=bool(with_certificate), merge=bool(merge))))
        if op == "download_certificate":
            return _render_file(_run(
                lambda: _client().download_completion_certificate(request_id)))
        raise _bad(f"unknown op: {op!r}")

    @mcp.tool(annotations=ACTE)
    def zohosign_act(
        op: Literal["send_from_template", "remind", "cancel"],
        request_id: Optional[str] = None,
        template_id: Optional[str] = None,
        recipients: Optional[list[dict]] = None,
        request_name: Optional[str] = None,
        field_data: Optional[dict] = None,
        notes: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """⚠️ ACTS at Zoho Sign toward real recipients, in the connected account's name.
        Reading requests and templates is `zohosign`.

        Args:
            op: which action.
                · "send_from_template" — create a request from `template_id` and SEND
                  it for signature at once. `recipients` = one entry per recipient of
                  the template, same count, each with its `action_id` (from
                  `zohosign(op="get_template")`), `recipient_name`, `recipient_email`,
                  and optionally `action_type`, `role`, `private_notes`,
                  `verify_recipient`, `verification_type`. `field_data` prefills fields:
                  {"field_text_data": {<label>: value}, "field_boolean_data": {…},
                  "field_date_data": {…}}; a mandatory field must get a value.
                · "remind" — remind the recipients of `request_id` who have not signed.
                · "cancel" — recall `request_id` (Zoho's "recall"): signing stops, the
                  recipients can no longer view or sign it. Not reversible.
            request_name: op="send_from_template" — name of the request (the
                template's by default).
            notes: op="send_from_template" — message to every recipient.
            dry_run: validate and show what would happen, without calling Zoho's write.
        """
        if op == "send_from_template":
            _need(op, template_id=template_id, recipients=recipients)
            _hors_op(op, request_id=request_id)
            for i, r in enumerate(recipients):
                if not isinstance(r, dict) or not all(
                        r.get(k) for k in ("action_id", "recipient_name", "recipient_email")):
                    raise _bad(f"recipients[{i}]: action_id, recipient_name and "
                               "recipient_email are required.")
            client = _client()
            tpl = (_run(lambda: client.get_template(template_id)) or {}).get("templates") or {}
            attendus = {str(a.get("action_id")) for a in tpl.get("actions") or []
                        if isinstance(a, dict)}
            donnes = [str(r["action_id"]) for r in recipients]
            if attendus and sorted(donnes) != sorted(attendus):
                raise _bad(f"The template's recipients are {sorted(attendus)} (action_id); "
                           f"got {sorted(donnes)} — one entry per recipient, same count.")
            if dry_run:
                return {"dry_run": True, "would_send": {
                    "template_id": template_id,
                    "request_name": request_name or tpl.get("template_name"),
                    "recipients": [{k: r.get(k) for k in ("action_id", "recipient_name",
                                                           "recipient_email")}
                                   for r in recipients],
                    "prefilled": sorted(k for part in (field_data or {}).values()
                                        if isinstance(part, dict) for k in part)}}
            created = _run(lambda: client.create_from_template(
                template_id, recipients, request_name=request_name,
                field_data=field_data, notes=notes, quick_send=True))
            req = (created or {}).get("requests") or {}
            return {"sent": True, "request": _slim_request(req)}

        _need(op, request_id=request_id)
        _hors_op(op, template_id=template_id, recipients=recipients,
                 request_name=request_name, field_data=field_data, notes=notes)
        if op in ("remind", "cancel"):
            client = _client()
            if dry_run:
                req = (_run(lambda: client.get_request(request_id)) or {}).get("requests") or {}
                vue = _slim_request(req)
                if op == "remind":
                    vue["would_remind"] = [a for a in vue["recipients"]
                                           if str(a.get("action_status") or "").upper()
                                           != "SIGNED"]
                return {"dry_run": True, "op": op, "request": vue}
            res = _run(lambda: (client.remind if op == "remind" else client.recall)(request_id))
            return {"done": True, "op": op, "request_id": request_id,
                    "message": (res or {}).get("message")}
        raise _bad(f"unknown op: {op!r}")
