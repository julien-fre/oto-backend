"""Lightfield — agent-native CRM: accounts, contacts, opportunities, lists, notes,
tasks, meetings, emails.

Wraps `oto.tools.lightfield.client.LightfieldClient` (API v1, Bearer `sk_lf_…`).
keyed `api_key`, byo-only: each organization sets ITS key, on ITS workspace — there
is no shared oto key, and there cannot be one (the data is the customer's).

**Nine tools, one per business object**, verb in `op=`. The grouping follows the object
and not a vague category: Lightfield's 29 scopes are themselves per object ×
verb, so a tool boundary = a permission boundary, and a missing scope
fails ONE tool whose message can name the scope at fault.

⚠️ **The field model is SPECIFIC TO EACH WORKSPACE.** A record carries
`fields: {key: {value, valueType}}` where the keys are defined by the customer, not by
Lightfield. No key is hard-coded here: `op="definitions"` discovers them, and
every write VALIDATES its keys against the definitions BEFORE calling the API — an unknown
key is refused by naming the valid keys, never swallowed as a 400.

⚠️ **`op="search"` and `op="get"` are not interchangeable.** Search serves an
index that may lag (vendor docs); re-reading a write through search
may return the state from BEFORE. After a write, re-read with `op="get"`.

Outputs are PROJECTED by default (id, date, link, flattened `key: value` fields); the
relationships are dropped and the response SAYS so (`projection` block). `full=True` returns
the raw record. A CRM returns fat records: without projection, a
25-row search drowns the agent's context.

Writes: `dry_run` everywhere, default `False` — EXCEPT email sending, the only action that
leaves the platform and reaches a real person, dry-run by DEFAULT.

Calls to the client are written in plain form (`_client().list_accounts(…)`): that is what
makes them verifiable by the version-skew probe (`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# "CRM core" read scopes: a key that has NONE authenticates but cannot do
# anything useful — the probe must say so rather than return a misleading green.
_CORE_READ_SCOPES = ("accounts:read", "contacts:read", "opportunities:read")

# Dropped from the default projection: fat, and rarely what one reads in order to CHOOSE.
_DROPPED = ("relationships",)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    code, param = body.get("code"), body.get("param")
    if status == 401:
        return ("Lightfield rejected the API key (401) — check the key configured on "
                "this connector (Lightfield: Settings → API keys).")
    if status == 403:
        return (f"Lightfield denied access (403{f', {code}' if code else ''}) — the key "
                "exists but lacks the scope for this operation. Scopes are "
                "chosen at key CREATION: you must create a new one "
                "with the missing scope, it cannot be added afterwards.")
    if status == 404:
        return "Lightfield: resource not found (404) — check the identifier."
    if status == 409:
        return ("Lightfield: conflict (409) — the record changed in the meantime. "
                "Re-read it with op='get' then retry on the fresh state.")
    if status in (400, 422):
        if code in ("unknown_field", "unknown_relationship"):
            return (f"Lightfield does not know this field in THIS workspace "
                    f"({code}{f': {param}' if param else ''}). Fields are specific "
                    "to each workspace: call op='definitions' on this object to "
                    "read the valid keys.")
        return (f"Lightfield rejected the request (HTTP {status}"
                f"{f', {code}' if code else ''}): {e.body}")
    if status == 429:
        return "Lightfield: too many requests (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"Lightfield is temporarily unavailable (HTTP {status}) — retry later."
    return f"Lightfield rejected the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: `/auth/validate` (free, NO scope required)
    — then we require at least one core read scope.

    Testing auth alone would return a misleading green: Lightfield's scopes are
    chosen at key creation, so a perfectly valid key ticked
    without `accounts/contacts/opportunities:read` answers 200 here and will fail on EVERY
    real call. It is the Zoho lesson, identically.
    """
    from oto.tools.lightfield.client import LightfieldClient, scope_granted
    info = LightfieldClient(api_key=fields["key"]).validate()
    if not info.get("active"):
        raise ValueError("Lightfield reports that this key is not active.")
    if not any(scope_granted(info, s) for s in _CORE_READ_SCOPES):
        granted = info.get("scopes") or []
        raise ValueError(
            "The key is valid but carries no CRM read scope "
            f"(granted: {granted or 'none'}). Recreate a Lightfield key ticking "
            "at least accounts:read, contacts:read or opportunities:read — scopes "
            "are chosen at creation and cannot be added afterwards.")


def _flatten(record: Any) -> Any:
    """`fields: {key: {value, valueType}}` → `fields: {key: value}`, relationships
    dropped. `valueType` is a transport detail: the agent wants the value."""
    if not isinstance(record, dict):
        return record
    out = {k: v for k, v in record.items() if k not in _DROPPED and k != "fields"}
    raw = record.get("fields")
    if isinstance(raw, dict):
        out["fields"] = {
            k: (v.get("value") if isinstance(v, dict) else v) for k, v in raw.items()
        }
    return out


def _project(payload: Any, full: bool) -> Any:
    """`full=True` → payload UNCHANGED. Otherwise we flatten and NAME what is missing:
    an output silently truncated makes the agent believe it has read everything."""
    if full or not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if isinstance(data, list):
        out = dict(payload)
        out["data"] = [_flatten(r) for r in data]
    elif isinstance(data, dict):
        out = dict(payload)
        out["data"] = _flatten(data)
    else:
        out = _flatten(payload)
    out["projection"] = {
        "dropped": list(_DROPPED),
        "flattened": "fields[].value",
        "how_to_get_everything": "full=True",
    }
    return out


def _field_keys(definitions: Any) -> set:
    """The field keys declared by THIS workspace (`fieldDefinitions` is a MAP
    key → definition)."""
    if not isinstance(definitions, dict):
        return set()
    defs = definitions.get("fieldDefinitions")
    return set(defs) if isinstance(defs, dict) else set()


def _check_fields(payload: dict, definitions: Any, obj: str) -> None:
    """Refuses a field key that THIS workspace does not declare, naming the valid
    keys. Without this the API returns a 400 `unknown_field` — accurate, but silent on what
    WOULD have worked, and the agent retries at random."""
    fields = payload.get("fields")
    if not isinstance(fields, dict) or not fields:
        return
    known = _field_keys(definitions)
    if not known:                      # unreadable definitions: do not block
        return
    unknown = sorted(set(fields) - known)
    if unknown:
        raise _bad(
            f"Unknown fields on `{obj}` in this workspace: {unknown}. "
            f"Valid keys: {sorted(known)}. "
            "Fields are specific to each Lightfield workspace.")


# `filters` is SPLATTED next to `limit`/`offset`: a filter key bearing one of
# these two names would raise a TypeError ("multiple values for keyword argument") that
# `_run` does not translate — the agent would receive an opaque internal error instead of a refusal
# that names the dedicated parameter.
_RESERVED_FILTERS = ("limit", "offset")


def _check_filters(filters: Any) -> None:
    clash = sorted(k for k in (filters or {}) if k in _RESERVED_FILTERS)
    if clash:
        raise _bad(f"`filters` cannot carry {clash}: pagination goes through the "
                   "tool's dedicated `limit` and `offset` parameters.")


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.lightfield.client import LightfieldClient

    connector_verify.register("lightfield", _verify)

    def _client() -> LightfieldClient:
        key, _ = access.resolve_api_key("lightfield")
        return LightfieldClient(api_key=key)

    def _run(fn):
        """Translates a Lightfield refusal into an actionable tool error."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- factory for the three "core" objects -------------------------------
    # accounts / contacts / opportunities have EXACTLY the same surface: a
    # factory avoids three copies that would diverge at the first fix.

    def _core_object(obj: str, list_fn, get_fn, create_fn, update_fn, defs_fn):
        def handler(op, record_id, fields, filters, limit, offset, dry_run, full):
            if op == "definitions":
                return _run(defs_fn)
            if op == "search":
                _check_filters(filters)
                return _project(
                    _run(lambda: list_fn(limit=limit, offset=offset, **(filters or {}))),
                    full)
            if op == "get":
                if not record_id:
                    raise _bad(f"op='get': `record_id` required (id of a {obj}).")
                return _project(_run(lambda: get_fn(record_id)), full)
            if op == "upsert":
                if not isinstance(fields, dict) or not fields:
                    raise _bad("op='upsert': `fields` required "
                               "(key → value, keys read by op='definitions').")
                payload = {"fields": fields}
                _check_fields(payload, _run(defs_fn), obj)
                if dry_run:
                    return {"dry_run": True,
                            "would": "update" if record_id else "create",
                            "record_id": record_id, "payload": payload}
                if record_id:
                    return _project(_run(lambda: update_fn(record_id, payload)), full)
                return _project(_run(lambda: create_fn(payload)), full)
            raise _bad(f"Invalid `op`: {op!r} "
                       "(expected: search | get | upsert | definitions).")
        return handler

    _accounts = _core_object(
        "account",
        lambda **kw: _client().list_accounts(**kw),
        lambda i: _client().get_account(i),
        lambda p: _client().create_account(p),
        lambda i, p: _client().update_account(i, p),
        lambda: _client().account_definitions())
    _contacts = _core_object(
        "contact",
        lambda **kw: _client().list_contacts(**kw),
        lambda i: _client().get_contact(i),
        lambda p: _client().create_contact(p),
        lambda i, p: _client().update_contact(i, p),
        lambda: _client().contact_definitions())
    _opportunities = _core_object(
        "opportunity",
        lambda **kw: _client().list_opportunities(**kw),
        lambda i: _client().get_opportunity(i),
        lambda p: _client().create_opportunity(p),
        lambda i, p: _client().update_opportunity(i, p),
        lambda: _client().opportunity_definitions())

    @mcp.tool()
    def lightfield_accounts(
        op: Literal["search", "get", "upsert", "definitions"] = "search",
        record_id: Optional[str] = None,
        fields: Optional[dict] = None,
        filters: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield accounts — the COMPANIES in the CRM: search them, read one, create
        or update one, or discover the field keys of this workspace.

        `op`:
        - **"search"** (default): filtered list. WARNING: served from a search index
          that may LAG behind recent writes — after writing, read back with op="get".
        - **"get"**: one record by `record_id`, read DIRECTLY (always current).
        - **"upsert"**: creates when `record_id` is omitted, updates when given.
          Field keys are validated against this workspace's definitions BEFORE the
          call; an unknown key is refused and the valid ones are named.
        - **"definitions"**: the field and relationship keys THIS workspace declares.
          Call it before your first write — keys are per-workspace, never universal.

        Args:
            op: "search" | "get" | "upsert" | "definitions".
            record_id: op="get" (required) / op="upsert" (present = update).
            fields: op="upsert" — `{field_key: value}`, keys from op="definitions".
            filters: op="search" — `{field_key: value}` filters, and relationship
                filters keyed by relationship slug.
            limit: op="search" — 1..25 (the API caps at 25); paginate with `offset`.
            offset: op="search" — start of the page.
            dry_run: op="upsert" — validate and echo the payload, write nothing.
            full: return the raw record (relationships, valueType) instead of the
                projected one.
        """
        return _accounts(op, record_id, fields, filters, limit, offset, dry_run, full)

    @mcp.tool()
    def lightfield_contacts(
        op: Literal["search", "get", "upsert", "definitions"] = "search",
        record_id: Optional[str] = None,
        fields: Optional[dict] = None,
        filters: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield contacts — the PEOPLE in the CRM: search them, read one, create or
        update one, or discover the field keys of this workspace.

        `op`:
        - **"search"** (default): filtered list. WARNING: served from a search index
          that may LAG behind recent writes — after writing, read back with op="get".
        - **"get"**: one record by `record_id`, read DIRECTLY (always current).
        - **"upsert"**: creates when `record_id` is omitted, updates when given.
          Field keys are validated against this workspace's definitions BEFORE the
          call; an unknown key is refused and the valid ones are named.
        - **"definitions"**: the field and relationship keys THIS workspace declares.
          Call it before your first write — keys are per-workspace, never universal.

        Args:
            op: "search" | "get" | "upsert" | "definitions".
            record_id: op="get" (required) / op="upsert" (present = update).
            fields: op="upsert" — `{field_key: value}`, keys from op="definitions".
            filters: op="search" — `{field_key: value}` filters, and relationship
                filters keyed by relationship slug.
            limit: op="search" — 1..25 (the API caps at 25); paginate with `offset`.
            offset: op="search" — start of the page.
            dry_run: op="upsert" — validate and echo the payload, write nothing.
            full: return the raw record (relationships, valueType) instead of the
                projected one.
        """
        return _contacts(op, record_id, fields, filters, limit, offset, dry_run, full)

    @mcp.tool()
    def lightfield_opportunities(
        op: Literal["search", "get", "upsert", "definitions"] = "search",
        record_id: Optional[str] = None,
        fields: Optional[dict] = None,
        filters: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield opportunities — the DEALS in the CRM: search them, read one, create
        or update one, or discover the field keys of this workspace.

        `op`:
        - **"search"** (default): filtered list. WARNING: served from a search index
          that may LAG behind recent writes — after writing, read back with op="get".
        - **"get"**: one record by `record_id`, read DIRECTLY (always current).
        - **"upsert"**: creates when `record_id` is omitted, updates when given.
          Field keys are validated against this workspace's definitions BEFORE the
          call; an unknown key is refused and the valid ones are named.
        - **"definitions"**: the field and relationship keys THIS workspace declares.
          Call it before your first write — keys are per-workspace, never universal.

        Args:
            op: "search" | "get" | "upsert" | "definitions".
            record_id: op="get" (required) / op="upsert" (present = update).
            fields: op="upsert" — `{field_key: value}`, keys from op="definitions".
            filters: op="search" — `{field_key: value}` filters, and relationship
                filters keyed by relationship slug.
            limit: op="search" — 1..25 (the API caps at 25); paginate with `offset`.
            offset: op="search" — start of the page.
            dry_run: op="upsert" — validate and echo the payload, write nothing.
            full: return the raw record (relationships, valueType) instead of the
                projected one.
        """
        return _opportunities(op, record_id, fields, filters, limit, offset, dry_run, full)

    # --- lists --------------------------------------------------------------

    @mcp.tool()
    def lightfield_lists(
        op: Literal["list", "get", "members", "upsert"] = "list",
        list_id: Optional[str] = None,
        of: Literal["accounts", "contacts", "opportunities"] = "accounts",
        fields: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield lists — the curated selections of accounts, contacts or deals.

        `op`:
        - **"list"** (default): the lists of the workspace.
        - **"get"**: one list by `list_id`.
        - **"members"**: what is IN the list — `of` picks accounts | contacts |
          opportunities. ⚠️ Needs TWO scopes: `lists:read` AND the read scope of the
          member type, so this can fail where op="get" succeeds.
        - **"upsert"**: creates when `list_id` is omitted, updates when given.

        Args:
            op: "list" | "get" | "members" | "upsert".
            list_id: op="get" / "members" (required) / "upsert" (present = update).
            of: op="members" — which member type to read.
            fields: op="upsert" — `{field_key: value}` (e.g. the list name).
            limit: 1..25 (the API caps at 25); paginate with `offset`.
            offset: start of the page.
            dry_run: op="upsert" — validate and echo the payload, write nothing.
            full: raw records instead of projected ones.
        """
        if op == "list":
            return _project(_run(lambda: _client().list_lists(limit=limit, offset=offset)),
                            full)
        if op == "get":
            if not list_id:
                raise _bad("op='get': `list_id` required.")
            return _project(_run(lambda: _client().get_list(list_id)), full)
        if op == "members":
            if not list_id:
                raise _bad("op='members': `list_id` required.")
            # Lambdas and not bound methods: the bound form built all THREE
            # clients (hence three `resolve_api_key` — vault read + decryption)
            # to keep only one. The call stays written IN PLAIN FORM, the only form the
            # version-skew probe can read.
            fn = {
                "accounts": lambda: _client().list_accounts_of_list(
                    list_id, limit=limit, offset=offset),
                "contacts": lambda: _client().list_contacts_of_list(
                    list_id, limit=limit, offset=offset),
                "opportunities": lambda: _client().list_opportunities_of_list(
                    list_id, limit=limit, offset=offset),
            }[of]
            return _project(_run(fn), full)
        if op == "upsert":
            if not isinstance(fields, dict) or not fields:
                raise _bad("op='upsert': `fields` required.")
            payload = {"fields": fields}
            if dry_run:
                return {"dry_run": True, "would": "update" if list_id else "create",
                        "list_id": list_id, "payload": payload}
            if list_id:
                return _project(_run(lambda: _client().update_list(list_id, payload)), full)
            return _project(_run(lambda: _client().create_list(payload)), full)
        raise _bad(f"Invalid `op`: {op!r} (expected: list | get | members | upsert).")

    # --- notes & tasks ------------------------------------------------------

    @mcp.tool()
    def lightfield_notes(
        op: Literal["create", "definitions"] = "create",
        fields: Optional[dict] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield notes — write a note onto the CRM (attached to an account,
        contact or opportunity through a relationship field).

        `op`:
        - **"create"** (default): writes the note. Field keys validated against this
          workspace's definitions first.
        - **"definitions"**: the field and relationship keys notes accept here —
          including how to attach the note to a record.

        Args:
            op: "create" | "definitions".
            fields: op="create" — `{field_key: value}`, keys from op="definitions".
            dry_run: validate and echo the payload, write nothing.
            full: raw record instead of the projected one.
        """
        if op == "definitions":
            return _run(lambda: _client().note_definitions())
        if op != "create":
            raise _bad(f"Invalid `op`: {op!r} (expected: create | definitions).")
        if not isinstance(fields, dict) or not fields:
            raise _bad("op='create': `fields` required "
                       "(key → value, keys read by op='definitions').")
        payload = {"fields": fields}
        _check_fields(payload, _run(lambda: _client().note_definitions()), "note")
        if dry_run:
            return {"dry_run": True, "would": "create", "payload": payload}
        return _project(_run(lambda: _client().create_note(payload)), full)

    @mcp.tool()
    def lightfield_tasks(
        op: Literal["upsert", "definitions"] = "upsert",
        record_id: Optional[str] = None,
        fields: Optional[dict] = None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """Lightfield tasks — create or update a task in the CRM.

        `op`:
        - **"upsert"** (default): creates when `record_id` is omitted, updates when
          given. Field keys validated against this workspace's definitions first.
        - **"definitions"**: the field and relationship keys tasks accept here.

        Args:
            op: "upsert" | "definitions".
            record_id: present = update that task, omitted = create a new one.
            fields: `{field_key: value}`, keys from op="definitions".
            dry_run: validate and echo the payload, write nothing.
            full: raw record instead of the projected one.
        """
        if op == "definitions":
            return _run(lambda: _client().task_definitions())
        if op != "upsert":
            raise _bad(f"Invalid `op`: {op!r} (expected: upsert | definitions).")
        if not isinstance(fields, dict) or not fields:
            raise _bad("op='upsert': `fields` required.")
        payload = {"fields": fields}
        _check_fields(payload, _run(lambda: _client().task_definitions()), "task")
        if dry_run:
            return {"dry_run": True, "would": "update" if record_id else "create",
                    "record_id": record_id, "payload": payload}
        if record_id:
            return _project(_run(lambda: _client().update_task(record_id, payload)), full)
        return _project(_run(lambda: _client().create_task(payload)), full)

    # --- meetings -----------------------------------------------------------

    @mcp.tool()
    def lightfield_meetings(
        op: Literal["search", "get", "definitions"] = "search",
        record_id: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        full: bool = False,
    ) -> dict:
        """Lightfield meetings — READ the meetings captured in the CRM.

        `op`:
        - **"search"** (default): filtered list (search index, may lag).
        - **"get"**: one meeting by `record_id`, read directly.
        - **"definitions"**: the field keys meetings carry here.

        Args:
            op: "search" | "get" | "definitions".
            record_id: op="get" — the meeting.
            filters: op="search" — `{field_key: value}` filters.
            limit: 1..25 (the API caps at 25); paginate with `offset`.
            offset: start of the page.
            full: raw records instead of projected ones.
        """
        if op == "definitions":
            return _run(lambda: _client().meeting_definitions())
        if op == "search":
            _check_filters(filters)
            return _project(_run(lambda: _client().list_meetings(
                limit=limit, offset=offset, **(filters or {}))), full)
        if op == "get":
            if not record_id:
                raise _bad("op='get': `record_id` required.")
            return _project(_run(lambda: _client().get_meeting(record_id)), full)
        raise _bad(f"Invalid `op`: {op!r} (expected: search | get | definitions).")

    # --- emails -------------------------------------------------------------

    @mcp.tool()
    def lightfield_emails(
        op: Literal["search", "get", "send", "draft"] = "search",
        record_id: Optional[str] = None,
        sender: Optional[str] = None,
        to: Optional[list[str]] = None,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: int = 25,
        offset: Optional[int] = None,
        dry_run: bool = True,
        full: bool = False,
    ) -> dict:
        """Lightfield emails — read the synced mail, or send a new one from a
        connected mailbox.

        `op`:
        - **"search"** (default): synced emails (search index, may lag).
        - **"get"**: one email by `record_id`.
        - **"send"**: SENDS A REAL EMAIL from `sender`. ⚠️ `dry_run` defaults to
          **True** here — the only tool of this connector whose effect leaves the
          platform and reaches a person. Pass `dry_run=False` deliberately to send.
        - **"draft"**: writes a draft into the mailbox of `sender`; nothing is sent.

        ⚠️ `sender` must be a mailbox CONNECTED to Lightfield (Google or Microsoft)
        and owned by the API key's user. Without one the API refuses and nothing
        leaves.

        ⚠️ Lightfield cannot reply or forward: a send always creates a NEW message,
        never a reply in an existing thread — quote the context yourself.

        Args:
            op: "search" | "get" | "send" | "draft".
            record_id: op="get" — the email.
            sender: op="send"/"draft" — the connected mailbox address (required).
            to: op="send"/"draft" — recipients.
            cc: carbon copy.
            bcc: blind carbon copy.
            subject: the subject line.
            body: the message body (text).
            filters: op="search" — `{field_key: value}` filters.
            limit: 1..25 (the API caps at 25); paginate with `offset`.
            offset: start of the page.
            dry_run: op="send"/"draft" — echo the payload, send nothing.
                DEFAULTS TO TRUE on this tool.
            full: raw records instead of projected ones.
        """
        if op == "search":
            _check_filters(filters)
            return _project(_run(lambda: _client().list_emails(
                limit=limit, offset=offset, **(filters or {}))), full)
        if op == "get":
            if not record_id:
                raise _bad("op='get': `record_id` required.")
            return _project(_run(lambda: _client().get_email(record_id)), full)
        if op not in ("send", "draft"):
            raise _bad(f"Invalid `op`: {op!r} (expected: search | get | send | draft).")
        if not sender or not sender.strip():
            raise _bad(f"op='{op}': `sender` required — the address of a mailbox "
                       "CONNECTED to Lightfield (Google or Microsoft) belonging to the "
                       "API key owner. Without it, nothing can go out.")
        payload: dict = {"from": sender.strip()}
        for k, v in (("to", to), ("cc", cc), ("bcc", bcc)):
            if v:
                payload[k] = list(v)
        if subject:
            payload["subject"] = subject
        if body:
            payload["messageBody"] = {"content": body}
        if op == "send" and not payload.get("to"):
            raise _bad("op='send': `to` required (at least one recipient).")
        if dry_run:
            return {"dry_run": True, "would": op, "payload": payload,
                    "note": ("Nothing was sent. Call again with dry_run=False to "
                             + ("send." if op == "send" else "write the draft."))}
        if op == "send":
            return _run(lambda: _client().send_email(payload))
        return _run(lambda: _client().draft_email(payload))

    # --- object types -------------------------------------------------------

    @mcp.tool()
    def lightfield_objects(
        op: Literal["list", "definitions"] = "list",
        object_type: Optional[str] = None,
    ) -> dict:
        """Lightfield object types — what kinds of records THIS workspace has,
        including the custom ones, and the field keys of any of them.

        `op`:
        - **"list"** (default): `{data: [{label, objectType}]}` — `objectType` is the
          slug to pass back as `object_type`.
        - **"definitions"**: the field and relationship keys of `object_type`. Use it
          for custom objects; the standard ones have op="definitions" on their own
          tool.

        Args:
            op: "list" | "definitions".
            object_type: op="definitions" — the slug from op="list".
        """
        if op == "list":
            return _run(lambda: _client().list_object_types())
        if op != "definitions":
            raise _bad(f"Invalid `op`: {op!r} (expected: list | definitions).")
        if not object_type:
            raise _bad("op='definitions': `object_type` required (slug read by op='list').")
        return _run(lambda: _client().object_definitions(object_type))
