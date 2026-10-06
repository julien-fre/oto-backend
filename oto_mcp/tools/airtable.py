"""Airtable — bases, tables, fields, rows, comments, attachments, CSV sync.

Covers the WHOLE "Base data" section of the Airtable Web API (records CRUD + upsert,
comments CRUD, attachment upload, CSV sync) and the companions without which an
agent can do nothing: a base's schema (tables + fields), creating/renaming
tables and fields, the list of granted bases, the token's identity.

Key resolved per call via `access.resolve_api_key("airtable")` — **BYO only** (user or
org): an Airtable PAT is attached to bases explicitly granted in a given
workspace, a shared platform key would make no sense (it would expose
Otomata's bases to every org).

**Consolidated surface (ADR 0047 §Amendment)**: one tool per OBJECT, the verb as an `op`
parameter — 22 endpoints → 7 tools. The split follows the homogeneity of the PARAMETERS:

- `airtable_record` (8 → 1) — everything is keyed by `base_id` + `table`; upsert is
  just a `performUpsert` on the same PATCH as update, so not a separate tool.
- `airtable_comment` (4 → 1) — deeper anchoring (`+ record_id`, `+ comment_id`) and
  `text`/`parent_comment_id` overlap no record parameter.
- `airtable_table` (3 → 1) — the CONTAINER: `name`, `description`, `fields[]`.
- `airtable_field` (3 → 1) — stays separate from `airtable_table` for the same reason
  as `attio_object` / `attio_attribute`: `type` + `options` (one object shape PER
  Airtable field type) exist nowhere else, merging them would make
  `name`/`description` carry two meanings depending on the op. `op="list"` = the base schema filtered on
  the table: a real read, which gives the stable `fld…` ids.
- `airtable_base` (3 → 1) — the only tool whose entry is not keyed by a base.
- `airtable_attachment` (1) and `airtable_sync` (1) stay on their own: different HOST
  (`content.airtable.com`, base64 body) for one, **raw `text/csv` body** and own rate
  limits (20 req/5 min) for the other. Putting a non-JSON path in
  `airtable_record` would pollute its signature for all the other verbs.

⚠️ This module WRITES to a REAL Airtable base. The default of every tool with an `op` is a
READ (`"list"` or `"schema"`): a call without `op` can neither write nor delete, and
an unknown op is refused BEFORE even resolving the key. The two tools that have
NO possible read (`airtable_attachment`, `airtable_sync`) deliberately have no
`op` parameter: a single verb has no verb to choose, and their payload parameters
(the file, the CSV) are all mandatory — no bare call can mutate.

⚠️ **`typecast` defaults to `False`, and that is a choice.** At Airtable it is not
a convenience conversion: it is a **schema mutation triggered by a data write**
(it creates the missing option of a select, even a record in the linked table of a
*linked record* field), and it only requires the `data.records:write` scope. An
`op="create"` with a misspelled value would therefore silently widen the schema of
a customer's base. Here the write fails outright; `typecast=True` is an
explicit gesture by the caller.

⚠️ **Batches and rate.** The API refuses more than **10 records per request** (create /
update / delete) and caps at **5 requests/second per base**; a 429 imposes **30 seconds**
of waiting. The oto-core client does not loop: this module splits into batches of 10,
spaces requests by 200 ms and caps at `_MAX_ITEMS` records per call — a cap
DERIVED from the 45 s invoke budget (`api/routes.py`), not guessed. On a 429 we **do not
wait** the 30 s (the call would die in a timeout without saying what was written): we stop and
return a partial receipt that NAMES what went through and what did not.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional, get_args

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# The ops of each object, reads → writes. Single source: the MCP SCHEMA
# (`Literal` → JSON `enum`), input validation AND the refusal message all derive from it.
_RecordOp = Literal["list", "get", "create", "update", "upsert", "delete"]
_CommentOp = Literal["list", "create", "update", "delete"]
_TableOp = Literal["schema", "create", "update"]
_FieldOp = Literal["list", "create", "update"]
_BaseOp = Literal["list", "whoami", "create"]

_RECORD_OPS = get_args(_RecordOp)
_COMMENT_OPS = get_args(_CommentOp)
_TABLE_OPS = get_args(_TableOp)
_FIELD_OPS = get_args(_FieldOp)
_BASE_OPS = get_args(_BaseOp)

# Airtable's HARD cap on multiple create/update/delete. Copied here rather than
# read from `AirtableClient`: the module must not depend on a class attribute for
# a value that governs the splitting. `test_batch_size_matches_the_core_client` breaks
# if oto-core changes its mind.
_BATCH_SIZE = 10
# Cap on items per call, DERIVED from the invoke budget (45 s, `api/routes.py`):
# 200 records = 20 requests of 10 × (200 ms courtesy + ~300 ms latency) ≈ 10 s.
_MAX_ITEMS = 200
# 5 requests/second per base on Airtable's side → 200 ms between two requests.
_RATE_DELAY = 0.2
# Cap on pages read at once — same reason (a base can have 100,000 rows).
_MAX_PAGES = 25
# Beyond this, the `filterByFormula` no longer fits in a query string: Airtable exposes
# `POST …/listRecords`, which takes the SAME criteria in the body. The switch is
# automatic and deterministic — an agent need not know about this URL limit.
_FORMULA_URL_LIMIT = 8000


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _one_of(name: str, values: tuple[str, ...]) -> str:
    """Refusal message DERIVED from the allowed values — an added op announces itself."""
    quoted = [f"'{v}'" for v in values]
    return f"{name} must be " + ", ".join(quoted[:-1]) + " or " + quoted[-1]


def _need(value, name: str, op: str):
    """Mandatory argument for THIS op. An EMPTY value counts as absent: a
    `fields={}` on `op='update'` would be a PATCH that changes nothing and would pass for
    a success."""
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requires {name}")
    return value


def _exactly_one(solo, plural, solo_name: str, plural_name: str, op: str):
    """Mutually exclusive singular/plural pair — never a "list of 1" to
    interpret. The singular returns the result directly, the plural a batch receipt."""
    if (solo is None) == (plural is None):
        raise _bad(
            f"op='{op}' requires EXACTLY one of `{solo_name}` (a single one) or "
            f"`{plural_name}` (several), not both and not neither."
        )


def _upstream_message(e) -> str:
    """Translate an Airtable refusal into an actionable message — the code alone says nothing."""
    hints = {
        401: "invalid or revoked Airtable token (PAT `pat…`, airtable.com/create/tokens).",
        403: "the token lacks the required scope, OR this base was not granted to it "
             "— both are fixed in the PAT settings.",
        404: "base, table, field or record not found — check `base_id` (app…), the "
             "table name/id, and that the base is actually granted to the token.",
        422: "Airtable refused the data: unknown field name, incompatible type, or "
             "missing select option (in the latter case, `typecast=True` would create it).",
        429: "Airtable rate limit reached (5 req/s per base) — Airtable requires "
             "30 seconds before retrying.",
    }
    hint = hints.get(getattr(e, "status_code", None), "")
    return f"{e}" + (f" — {hint}" if hint else "")


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe — TWO stages, because auth alone proves nothing.

    Airtable's dominant failure mode is not a bad token: it is a valid PAT,
    all scopes ticked, to which no base was ever granted. `GET /meta/bases`
    then answers **200 with an empty list**, not an error — a probe that stops at
    auth would therefore validate a credential unable to read anything.
    """
    from oto.tools.airtable.client import AirtableClient

    client = AirtableClient(api_key=fields["key"])
    client.whoami()  # auth
    bases = (client.list_bases() or {}).get("bases") or []  # scope + grant
    if not bases:
        raise RuntimeError(
            "valid Airtable token, but NO base is granted to it: open "
            "airtable.com/create/tokens, edit the token and add the base(s) "
            "under \"Access\" (scopes alone are not enough)."
        )


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _check_items(items: list, name: str) -> list:
    if not isinstance(items, list):
        raise _bad(f"`{name}` must be a list.")
    if len(items) > _MAX_ITEMS:
        raise _bad(
            f"`{name}`: {len(items)} items for a maximum of {_MAX_ITEMS} per call "
            f"(Airtable caps at 10 per request and 5 requests/s per base; beyond that "
            f"the call would exceed its time budget). Split the call."
        )
    return items


def _norm_records(records: list, *, need_id: bool, op: str) -> list[dict]:
    """Normalize to Airtable's `{"id"?: …, "fields": {…}}` shape.

    An item WITHOUT a `fields` key is taken to be the field map itself
    (`{"Name": "Ada"}` ⟹ `{"fields": {"Name": "Ada"}}`): this is the shape a caller
    writes spontaneously. Accepted corollary: a table with a column literally named
    "fields" must use the explicit form.
    """
    out: list[dict] = []
    for i, item in enumerate(records):
        if not isinstance(item, dict):
            raise _bad(f"`records[{i}]` must be an object, got {type(item).__name__}.")
        if "fields" in item and isinstance(item["fields"], dict):
            rec = {"fields": item["fields"]}
            if item.get("id"):
                rec["id"] = item["id"]
        else:
            rec = {"fields": {k: v for k, v in item.items() if k != "id"}}
            if item.get("id"):
                rec["id"] = item["id"]
        if not rec["fields"]:
            raise _bad(f"`records[{i}]` has no field to write.")
        if need_id and "id" not in rec:
            raise _bad(
                f"op='{op}': `records[{i}]` has no `id`. To match rows "
                f"on a business value rather than on their id, use op='upsert'."
            )
        out.append(rec)
    return out


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.airtable.client import AirtableClient

    connector_verify.register("airtable", _verify)

    def _client() -> AirtableClient:
        key, _ = access.resolve_api_key("airtable")
        return AirtableClient(api_key=key)

    def _run(fn):
        """Translate an Airtable refusal (or a client guard) into an actionable error."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _batched(items: list, call, *, key: str, also: tuple[str, ...] = ()) -> dict:
        """Split into batches of 10, space out the requests, return an HONEST receipt.

        The loop lives HERE and not in the oto-core client because this is where it can be
        reported on: the invoke budget (45 s) and the partial receipt belong to the
        layer that answers the agent. A client that quietly split would return "it
        worked" or raise, without ever being able to say "30 written out of 50".

        `call(chunk)` makes ONE request. Three failure regimes, deliberately distinct:
        - **401/403** → the key is wrong for everything that follows: raise right away
          rather than repeat the same refusal N times.
        - **429** → we STOP (Airtable wants 30 s, the invoke budget is 45 s) and
          return the partial receipt: the caller knows exactly where to resume.
        - **other 4xx** → the failure is specific to this batch: record it and carry on.
        """
        done: list = []
        # SECONDARY lists returned by Airtable that a batch must not lose: on an
        # upsert, `createdRecords`/`updatedRecords` are the only thing saying what was
        # CREATED rather than matched — dropping them would return a receipt that
        # counts right and does not answer the question asked of an upsert.
        extra: dict[str, list] = {k: [] for k in also}
        failed: list[dict] = []
        aborted: Optional[str] = None
        chunks = _chunks(items, _BATCH_SIZE)
        for n, chunk in enumerate(chunks):
            if n:
                time.sleep(_RATE_DELAY)
            first = n * _BATCH_SIZE
            try:
                result = call(chunk) or {}
            except UpstreamHTTPError as e:
                if e.status_code in (401, 403):
                    raise _bad(_upstream_message(e))
                if e.status_code == 429:
                    aborted = "rate_limit"
                    failed.append({"items": [first, first + len(chunk) - 1],
                                   "error": _upstream_message(e)})
                    break
                failed.append({"items": [first, first + len(chunk) - 1],
                               "error": _upstream_message(e)})
                continue
            except ValueError as e:
                # Client guard (batch size): that is a defect of THIS code, not an
                # Airtable refusal. It must shout once, not disguise itself as N
                # upstream failures in the receipt.
                raise _bad(str(e))
            done.extend(result.get(key) or [])
            for k in also:
                extra[k].extend(result.get(k) or [])
        receipt: dict[str, Any] = {
            "total": len(items),
            "succeeded": len(done),
            "failed": failed,
            key: done,
        }
        receipt.update({k: v for k, v in extra.items() if v})
        if aborted:
            receipt["aborted"] = aborted
            receipt["resume_hint"] = (
                f"{len(done)} item(s) processed before the rate limit. Wait 30 s and "
                f"retry with the remaining items."
            )
        return receipt

    def _paginate(fetch, key: str, limit: int) -> dict:
        """Follow Airtable's opaque `offset` up to `limit` items or `_MAX_PAGES`.

        `fetch(offset, page_size)` returns ONE page. The requested size SHRINKS as
        the cap approaches, so that the last page lands exactly on it — otherwise
        the final page would have to be cut, and the returned `offset` would resume AFTER the
        discarded items: a handful of rows would silently vanish, even though the
        response announces where to resume. Nothing is ever truncated here; if pages
        remain, the response SAYS so (`more` + `offset`).
        """
        items: list = []
        offset: Optional[str] = None
        pages = 0
        while True:
            page = fetch(offset, max(1, min(100, limit - len(items)))) or {}
            items.extend(page.get(key) or [])
            offset = page.get("offset")
            pages += 1
            if not offset or len(items) >= limit or pages >= _MAX_PAGES:
                break
            time.sleep(_RATE_DELAY)
        out: dict[str, Any] = {key: items, "count": len(items)}
        if offset:
            out["offset"] = offset
            out["more"] = True
        return out

    def _tables_of(client: AirtableClient, base_id: str) -> list[dict]:
        return (client.get_base_schema(base_id) or {}).get("tables") or []

    # ==================================================================
    # Records
    # ==================================================================

    @mcp.tool()
    def airtable_record(
        base_id: str,
        table: str,
        op: _RecordOp = "list",
        record_id: Optional[str] = None,
        record_ids: Optional[list[str]] = None,
        fields: Optional[dict] = None,
        records: Optional[list[dict]] = None,
        select_fields: Optional[list[str]] = None,
        filter_by_formula: Optional[str] = None,
        view: Optional[str] = None,
        sort: Optional[list[dict]] = None,
        max_records: int = 100,
        merge_on: Optional[list[str]] = None,
        typecast: bool = False,
        replace: bool = False,
        cell_format: Optional[str] = None,
        time_zone: Optional[str] = None,
        user_locale: Optional[str] = None,
        return_fields_by_field_id: bool = False,
    ) -> dict:
        """A row in an Airtable table: list, read, create, update, upsert, delete.

        `base_id` is the base (`appXXXXXXXX`, from `airtable_base`), `table` is a table
        name or id (`tblXXXXXXXX`, from `airtable_table`). Ids are the stable path —
        names break silently when someone renames a column or a table.

        `op`:
        - **"list"** (default): rows of the table, newest page first. Narrow with
          `filter_by_formula`, `view`, `sort`, `select_fields`. Follows Airtable's
          pagination up to `max_records`; if rows remain, the reply carries
          `more: true` and the `offset` to resume from.
        - **"get"**: one row by `record_id`.
        - **"create"** — ⚠️ WRITES: one row (`fields`) or many (`records`, up to 200).
        - **"update"** — ⚠️ WRITES: one row (`record_id` + `fields`) or many (`records`,
          each carrying its `id`). PATCH by default: untouched fields keep their value.
        - **"upsert"** — ⚠️ WRITES: `records` matched against existing rows on
          `merge_on` (1-3 field names) — matched rows are updated, unmatched ones
          created. The reply counts each (`created` / `updated`) and lists their ids
          under `createdRecords` / `updatedRecords`.
        - **"delete"** — ⚠️ WRITES: one row (`record_id`) or many (`record_ids`).
          Irreversible.

        Writes go 10 rows per request with a courtesy delay, capped at 200 rows per
        call. If Airtable rate-limits mid-batch the call stops and returns a receipt
        naming what was written — it never silently half-succeeds.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: table name or id (`tblXXXXXXXX` — the stable one).
            op: list (default) | get | create | update | upsert | delete.
            record_id: op="get"/"update"/"delete" — a single row `recXXXXXXXX`.
            record_ids: op="delete" — several rows at once.
            fields: op="create"/"update" — one row's cells, `{"Name": "Ada", "Age": 36}`.
            records: op="create"/"update"/"upsert" — several rows. Either plain cell
                maps (`[{"Name": "Ada"}]`) or the explicit Airtable shape
                (`[{"id": "rec…", "fields": {…}}]`); `id` is required for "update".
            select_fields: op="list"/"get" — only return these columns. The first lever
                against a huge reply.
            filter_by_formula: op="list" — Airtable formula evaluated per row, e.g.
                `{Status}='Done'` or `AND({Score}>10, {Owner}='Ada')`. A very long
                formula switches to Airtable's POST form on its own.
            view: op="list" — restrict to a view's rows and order.
            sort: op="list" — `[{"field": "Name", "direction": "asc"|"desc"}]`.
            max_records: op="list" — how many rows to return (default 100).
            merge_on: op="upsert" — 1 to 3 field names used to match existing rows.
            typecast: op="create"/"update"/"upsert" — let Airtable coerce values AND
                create missing select options / linked records. Off by default: it
                mutates the base's schema from a data write.
            replace: op="update" — ⚠️ PUT instead of PATCH: every field NOT sent is
                CLEARED. Off by default.
            cell_format: op="list"/"get" — "json" (default) or "string" (formatted as
                the UI shows them; then `time_zone` and `user_locale` are required).
            time_zone: with cell_format="string", e.g. "Europe/Paris".
            user_locale: with cell_format="string", e.g. "fr".
            return_fields_by_field_id: key returned cells by `fld…` id instead of name.
        """
        # Refuse BEFORE any credential resolution: an unknown op never reaches
        # the client, so never, through a derived path, a write to the base.
        if op not in _RECORD_OPS:
            raise _bad(_one_of("op", _RECORD_OPS))
        client = _client()

        if op == "list":
            long_formula = len(filter_by_formula or "") > _FORMULA_URL_LIMIT

            def _page(off, size):
                if long_formula:
                    # Same criteria, in the BODY: a formula of this size would exceed the
                    # allowed URL length and Airtable would reject it.
                    body = {k: v for k, v in {
                        "fields": select_fields,
                        "filterByFormula": filter_by_formula,
                        "view": view,
                        "sort": sort,
                        "pageSize": size,
                        "cellFormat": cell_format,
                        "timeZone": time_zone,
                        "userLocale": user_locale,
                        "returnFieldsByFieldId": return_fields_by_field_id or None,
                        "offset": off,
                    }.items() if v is not None}
                    return client.list_records_post(base_id, table, body)
                return client.list_records(
                    base_id, table,
                    fields=select_fields,
                    filter_by_formula=filter_by_formula,
                    view=view,
                    sort=sort,
                    page_size=size,
                    cell_format=cell_format,
                    time_zone=time_zone,
                    user_locale=user_locale,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                    offset=off,
                )

            return _run(lambda: _paginate(_page, "records", max_records))

        if op == "get":
            return _run(lambda: client.get_record(
                base_id, table, _need(record_id, "record_id", op),
                cell_format=cell_format, time_zone=time_zone, user_locale=user_locale,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ))

        if op == "create":
            _exactly_one(fields, records, "fields", "records", op)
            if fields is not None:
                return _run(lambda: client.create_records(
                    base_id, table, [{"fields": fields}], typecast=typecast or None,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                ))
            items = _norm_records(
                _check_items(records, "records"), need_id=False, op=op)
            return _batched(items, lambda chunk: client.create_records(
                base_id, table, chunk, typecast=typecast or None,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records")

        if op == "update":
            _exactly_one(record_id, records, "record_id", "records", op)
            if record_id is not None:
                return _run(lambda: client.update_record(
                    base_id, table, record_id, _need(fields, "fields", op),
                    replace=replace, typecast=typecast or None,
                    return_fields_by_field_id=return_fields_by_field_id or None,
                ))
            items = _norm_records(
                _check_items(records, "records"), need_id=True, op=op)
            return _batched(items, lambda chunk: client.update_records(
                base_id, table, chunk, replace=replace, typecast=typecast or None,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records")

        if op == "upsert":
            merge = _need(merge_on, "merge_on (1 to 3 field names)", op)
            if not isinstance(merge, list) or not 1 <= len(merge) <= 3:
                raise _bad("`merge_on` must be a list of 1 to 3 field names.")
            items = _norm_records(
                _check_items(_need(records, "records", op), "records"),
                need_id=False, op=op)
            upsert = {"fieldsToMergeOn": merge}
            receipt = _batched(items, lambda chunk: client.update_records(
                base_id, table, chunk, replace=replace, typecast=typecast or None,
                perform_upsert=upsert,
                return_fields_by_field_id=return_fields_by_field_id or None,
            ), key="records", also=("createdRecords", "updatedRecords"))
            receipt["merged_on"] = merge
            receipt["created"] = len(receipt.get("createdRecords") or [])
            receipt["updated"] = len(receipt.get("updatedRecords") or [])
            return receipt

        # delete
        _exactly_one(record_id, record_ids, "record_id", "record_ids", op)
        if record_id is not None:
            return _run(lambda: client.delete_record(base_id, table, record_id))
        ids = _check_items(record_ids, "record_ids")
        return _batched(ids, lambda chunk: client.delete_records(base_id, table, chunk),
                        key="records")

    # ==================================================================
    # Comments
    # ==================================================================

    @mcp.tool()
    def airtable_comment(
        base_id: str,
        table: str,
        record_id: str,
        op: _CommentOp = "list",
        comment_id: Optional[str] = None,
        text: Optional[str] = None,
        parent_comment_id: Optional[str] = None,
        max_comments: int = 100,
    ) -> dict:
        """A comment on an Airtable row: list, create, update, delete.

        Comments live on a single row, so `base_id` + `table` + `record_id` are always
        required.

        `op`:
        - **"list"** (default): comments newest first, with author, reactions and
          `parentCommentId` for threaded replies.
        - **"create"** — ⚠️ WRITES: post `text`. Mention someone with `@[usrXXXXXXX]`;
          `parent_comment_id` replies inside an existing thread.
        - **"update"** — ⚠️ WRITES: rewrite a comment's `text`. A token can only edit
          comments written by its own user.
        - **"delete"** — ⚠️ WRITES: remove a comment. Deleting a thread's first comment
          deletes the whole thread.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: table name or id.
            record_id: the row the comments hang off, `recXXXXXXXX`.
            op: list (default) | create | update | delete.
            comment_id: op="update"/"delete" — `comXXXXXXXX`.
            text: op="create"/"update" — the comment body.
            parent_comment_id: op="create" — reply inside that thread.
            max_comments: op="list" — how many to return (default 100).
        """
        if op not in _COMMENT_OPS:
            raise _bad(_one_of("op", _COMMENT_OPS))
        client = _client()

        if op == "list":
            return _run(lambda: _paginate(
                lambda off, size: client.list_comments(
                    base_id, table, record_id, page_size=size, offset=off,
                ),
                "comments", max_comments,
            ))
        if op == "create":
            return _run(lambda: client.create_comment(
                base_id, table, record_id, _need(text, "text", op),
                parent_comment_id=parent_comment_id,
            ))
        if op == "update":
            return _run(lambda: client.update_comment(
                base_id, table, record_id,
                _need(comment_id, "comment_id", op), _need(text, "text", op),
            ))
        return _run(lambda: client.delete_comment(
            base_id, table, record_id, _need(comment_id, "comment_id", op)))

    # ==================================================================
    # Tables
    # ==================================================================

    @mcp.tool()
    def airtable_table(
        base_id: str,
        op: _TableOp = "schema",
        table_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[list[dict]] = None,
    ) -> dict:
        """A table inside a base: read the schema, create a table, rename one.

        `op`:
        - **"schema"** (default): every table of the base with its fields (id, name,
          type, options) and views. Pass `table_id` to narrow to one table. This is the
          only way to discover field names and types before writing rows — there is no
          "get one table" endpoint in the API.
        - **"create"** — ⚠️ WRITES: a new table from `name` + `fields`. The FIRST field
          becomes the primary field and must be a type allowed as primary (text, number,
          date, formula — not attachment or checkbox).
        - **"update"** — ⚠️ WRITES: rename / redescribe a table. Structure changes go
          through `airtable_field`.

        Args:
            base_id: the base, `appXXXXXXXX`.
            op: schema (default) | create | update.
            table_id: op="schema" — narrow to one table; op="update" — which table.
            name: op="create"/"update" — the table name.
            description: op="create"/"update" — up to 20 000 characters.
            fields: op="create" — `[{"name": …, "type": …, "options": {…}}]`. See
                `airtable_field` for the per-type `options` shapes.
        """
        if op not in _TABLE_OPS:
            raise _bad(_one_of("op", _TABLE_OPS))
        client = _client()

        if op == "schema":
            tables = _run(lambda: _tables_of(client, base_id))
            if table_id:
                tables = [t for t in tables
                          if t.get("id") == table_id or t.get("name") == table_id]
                if not tables:
                    raise _bad(
                        f"no table `{table_id}` in base {base_id} — call "
                        f"op='schema' without `table_id` to see the ones that exist."
                    )
            return {"tables": tables, "count": len(tables)}

        if op == "create":
            return _run(lambda: client.create_table(
                base_id, _need(name, "name", op), _need(fields, "fields", op),
                description=description,
            ))

        if name is None and description is None:
            raise _bad("op='update' requires `name` and/or `description`")
        return _run(lambda: client.update_table(
            base_id, _need(table_id, "table_id", op),
            name=name, description=description,
        ))

    # ==================================================================
    # Fields
    # ==================================================================

    @mcp.tool()
    def airtable_field(
        base_id: str,
        table_id: str,
        op: _FieldOp = "list",
        field_id: Optional[str] = None,
        name: Optional[str] = None,
        type: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> dict:
        """A column of a table: list the columns, add one, rename one.

        `op`:
        - **"list"** (default): the table's fields — `fld…` id, name, type and options.
          Read this before writing rows: it is what tells you the exact column names and
          which select options already exist.
        - **"create"** — ⚠️ WRITES: add a column. `options` is required by most types
          and its shape depends on `type`.
        - **"update"** — ⚠️ WRITES: rename / redescribe a column. Airtable does NOT let
          the API change an existing field's `type` or `options` — create a new field.

        Common `type` values and the `options` they need:
        `singleLineText`, `multilineText`, `email`, `url`, `phoneNumber` (no options) ·
        `number` `{"precision": 0}` · `percent`, `currency` `{"precision": 2,
        "symbol": "€"}` · `checkbox` `{"color": "greenBright", "icon": "check"}` ·
        `singleSelect`, `multipleSelects` `{"choices": [{"name": "Done"}]}` ·
        `date` `{"dateFormat": {"name": "iso"}}` · `dateTime` `{"dateFormat":
        {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "Europe/Paris"}` ·
        `multipleRecordLinks` `{"linkedTableId": "tbl…"}` · `multipleAttachments`,
        `rating` `{"max": 5, "icon": "star", "color": "yellowBright"}`.

        Args:
            base_id: the base, `appXXXXXXXX`.
            table_id: the table, `tblXXXXXXXX` (a name works too).
            op: list (default) | create | update.
            field_id: op="update" — `fldXXXXXXXX`.
            name: op="create"/"update" — the column name.
            type: op="create" — one of the Airtable field types above.
            description: op="create"/"update" — up to 20 000 characters.
            options: op="create" — the type-specific configuration above.
        """
        if op not in _FIELD_OPS:
            raise _bad(_one_of("op", _FIELD_OPS))
        client = _client()

        if op == "list":
            tables = _run(lambda: _tables_of(client, base_id))
            match = next((t for t in tables
                          if t.get("id") == table_id or t.get("name") == table_id), None)
            if match is None:
                known = ", ".join(f"{t.get('name')} ({t.get('id')})" for t in tables[:20])
                raise _bad(
                    f"no table `{table_id}` in base {base_id}. "
                    f"Existing tables: {known or '(none)'}"
                )
            fields = match.get("fields") or []
            return {
                "table": {"id": match.get("id"), "name": match.get("name"),
                          "primaryFieldId": match.get("primaryFieldId")},
                "fields": fields,
                "count": len(fields),
            }

        if op == "create":
            return _run(lambda: client.create_field(
                base_id, table_id, _need(name, "name", op), _need(type, "type", op),
                description=description, options=options,
            ))

        if type is not None or options is not None:
            raise _bad(
                "op='update' does not send `type` or `options` — the Airtable API does "
                "NOT allow changing the type or options of an existing field. "
                "Recreating the field (op='create') is the only way."
            )
        if name is None and description is None:
            raise _bad(
                "op='update' requires `name` and/or `description` — the Airtable API does "
                "NOT allow changing the `type` or `options` of an existing field."
            )
        return _run(lambda: client.update_field(
            base_id, table_id, _need(field_id, "field_id", op),
            name=name, description=description,
        ))

    # ==================================================================
    # Bases and token identity
    # ==================================================================

    @mcp.tool()
    def airtable_base(
        op: _BaseOp = "list",
        max_bases: int = 200,
        name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        tables: Optional[list[dict]] = None,
    ) -> dict:
        """The Airtable bases this token can reach, and who the token is.

        `op`:
        - **"list"** (default): every base granted to the token, with its
          `permissionLevel`. Start here — a `base_id` (`appXXXXXXXX`) is what every
          other Airtable tool needs. An EMPTY list means the token is valid but no base
          was granted to it (fix that in the token's settings, not in the scopes).
        - **"whoami"**: the user behind the token, and its scopes. Says nothing about
          which bases are reachable — that is "list".
        - **"create"** — ⚠️ WRITES: a new base in a workspace (`wspXXXXXXXX`) with at
          least one table. Airtable has no delete-base endpoint: this cannot be undone
          from the API.

        Args:
            op: list (default) | whoami | create.
            max_bases: op="list" — how many bases to return (default 200).
            name: op="create" — the base name.
            workspace_id: op="create" — `wspXXXXXXXX`, from the Airtable URL.
            tables: op="create" — `[{"name": …, "fields": [{"name": …, "type": …}]}]`.
                The first field of each table becomes its primary field.
        """
        if op not in _BASE_OPS:
            raise _bad(_one_of("op", _BASE_OPS))
        client = _client()

        if op == "list":
            out = _run(lambda: _paginate(
                # `GET /meta/bases` has no `pageSize`: it returns 1000 bases per page,
                # so `size` is ignored here. `max_bases` bounds the number of PAGES read,
                # not the cut of the result — we never throw away what we have already read.
                lambda off, _size: client.list_bases(offset=off), "bases", max_bases))
            if not out["bases"]:
                out["hint"] = (
                    "No base granted to this token. Open airtable.com/create/tokens, "
                    "edit the token, and add the base(s) under \"Access\" — scopes "
                    "alone give access to nothing."
                )
            return out

        if op == "whoami":
            return _run(client.whoami)

        return _run(lambda: client.create_base(
            _need(name, "name", op),
            _need(workspace_id, "workspace_id", op),
            _need(tables, "tables", op),
        ))

    # ==================================================================
    # Attachments — no `op` parameter: a single verb, everything mandatory
    # ==================================================================

    @mcp.tool()
    def airtable_attachment(
        base_id: str,
        record_id: str,
        field: str,
        filename: str,
        content_type: str,
        file_base64: str,
    ) -> dict:
        """⚠️ WRITES: attach a file to an attachment column of a row.

        ADDS to the column — existing attachments are kept. Returns the updated row.

        The file travels base64-encoded and Airtable caps it at **5 MB**. For anything
        bigger, host the file somewhere public and write its URL into the column with
        `airtable_record(op="update", fields={"<column>": [{"url": "https://…"}]})` —
        Airtable fetches it itself, with no size limit of this kind.

        Args:
            base_id: the base, `appXXXXXXXX`.
            record_id: the row, `recXXXXXXXX`.
            field: the attachment column, name or `fldXXXXXXXX`.
            filename: the name the file gets in Airtable, with its extension.
            content_type: MIME type, e.g. "image/png", "application/pdf", "text/csv".
            file_base64: the file's bytes, base64-encoded (5 MB max).
        """
        client = _client()
        return _run(lambda: client.upload_attachment(
            base_id, record_id, field,
            filename=filename, content_type=content_type, file_b64=file_base64,
        ))

    # ==================================================================
    # CSV sync — no `op` parameter either
    # ==================================================================

    @mcp.tool()
    def airtable_sync(
        base_id: str,
        table: str,
        sync_id: str,
        csv_data: str,
    ) -> dict:
        """⚠️ WRITES: push raw CSV into a table set up as an Airtable "Sync API" source.

        This is NOT a CSV import into an ordinary table. The table must have been
        created in Airtable through the "Sync from other sources → API" flow, which is
        what produces `sync_id`; you find it in the synced table's settings. To load
        rows into a normal table, use `airtable_record(op="create")` instead.

        Each push REPLACES the synced content — the CSV is the source of truth, not an
        append. Limits: 10 000 rows, 500 columns, 2 MB per push, and 20 pushes per
        5 minutes per base (a tighter limit than the rest of the API).

        Args:
            base_id: the base, `appXXXXXXXX`.
            table: the synced table, name or `tblXXXXXXXX`.
            sync_id: the API endpoint sync id from the synced table's settings.
            csv_data: the CSV itself, header row included.
        """
        client = _client()
        result = _run(lambda: client.sync_csv(base_id, table, sync_id, csv_data))
        return result if isinstance(result, dict) else {"ok": True, "response": result}
