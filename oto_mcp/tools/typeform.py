"""Typeform — online forms: workspaces, forms, responses, webhooks.

Wraps `oto.tools.typeform.TypeformClient` (personal token as Bearer).
Credential resolved per call via `access.resolve_credential_fields("typeform")`
(byo user OR org, no platform key): `key` (secret) and `region` (us by
default, eu, eu2), which chooses the data center host.

Three modules for ONE connector, by object (the 500-line cap):
- here — the shared base (client, refusals, data center) and the responses:
  `typeform_workspaces`, `typeform_responses`, `typeform_responses_summary`,
  `typeform_delete_responses`;
- `typeform_formulaires` — `typeform_forms` (read, create, patch, replace, delete);
- `typeform_webhooks` — `typeform_webhooks`.

**What the tool layer adds to the transport**:
1. Tightened views by default (ADR 0047, `full=True` returns the raw payload): a
   list returns enough to choose, a definition returns its questions without screens,
   theme or logic — and the response NAMES what it removed (`omitted`).
2. **Readable responses.** The API returns `answers[]` in any order,
   each identified by its question's id, the value filed under a key that
   depends on its type. `typeform_responses` reads the form definition
   once per page and returns each response as `{question title:
   value}`; two questions with the same title are told apart by their id,
   never overwritten.
3. **The data center trap, closed.** Read outside the account's region, the
   responses come back EMPTY without error — and a deletion there is a silent
   no-op. The form definition carries the host of its responses
   (`_links.responses`): if it does not match the configured region, the tool
   REFUSES, naming the right region, instead of returning a credible zero.
4. **Two steps for what cannot be undone** (claap pattern): replacing or deleting
   a form, deleting responses, creating or deleting a webhook first return a
   preview (`dry_run: true`, read from Typeform, not echoed); only `confirm=True`
   writes.

**No argument is silently dropped**: an argument that an `op` does not use
is refused (`_refuse_ignored`, tally/claap pattern).

Client calls are written in plain sight (`_client().list_forms(…)`) for the
version probe (`test_tools_client_methods_exist`).

Checked against the public reference (Create API, Responses API, Webhooks API,
"EU Responses Data Center" page); **not tested live** — no Typeform token
available at writing time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import LECTURE

if TYPE_CHECKING:  # for `_client()`'s annotation only — never evaluated
    from oto.tools.typeform import TypeformClient

#: Where the user creates their token, in THEIR Typeform account.
WHERE_TO_CREATE = "Typeform → Account → Personal tokens"

#: Bound on a page of responses served to the agent. Upstream accepts 1000, but
#: responses are not projected (a long text is the data): the page
#: is the only size bound, and `total_items` says how many remain.
RESPONSES_MAX_PAGE = 200
RESPONSES_DEFAULT_PAGE = 25

#: Most response ids one deletion takes (the upstream bound).
DELETE_MAX_IDS = 1000

#: Every kind of response, so that a preview finds a partial one too.
ALL_RESPONSE_TYPES = ["completed", "partial", "started"]

#: What a response's tightened view removes.
RESPONSE_OMITTED = ("metadata", "landing_id", "token", "answers[].field.type")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided: Any) -> None:
    """A provided argument that THIS op does not use is an error of intent —
    otherwise `op="get"` with `search=` would return a form while letting
    one believe the search filtered. A `bool` flag is passed as `flag or None`:
    left at False, it was not provided."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _preview(would: str, what: Dict[str, Any], note: str) -> dict:
    """The first step of an irreversible write: what it would do, nothing sent."""
    return {"dry_run": True, would: what,
            "note": f"{note} Nothing was written: call again with confirm=True."}


def _region(value: Any) -> str:
    """The credential's `region` → the client's region. Empty = us. A value outside
    the declared set is refused at setup; here too, rather than aiming at a
    random host."""
    from oto.tools.typeform import REGIONS

    region = str(value or "us").strip().lower()
    if region not in REGIONS:
        raise _bad(f"Typeform: unknown data center {value!r} on this connector — "
                   f"expected one of {', '.join(REGIONS)}.")
    return region


def upstream_message(e: Any, what: str = "") -> str:
    """Un refus de Typeform (`UpstreamHTTPError`) en consigne actionnable."""
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    detail = body.get("description") or body.get("message") or body.get("code") or e.body
    if status == 401:
        return (f"Typeform rejected the token (401): it is unknown, revoked, or "
                f"belongs to another data center — tokens of « eu2 » "
                f"(api.typeform.eu) are distinct. Create one in {WHERE_TO_CREATE}.")
    if status == 403:
        return (f"Typeform refused this call (403): the token lacks the scope it "
                f"needs ({what or 'forms:read, responses:read, workspaces:read'}) "
                f"— generate a token with it in {WHERE_TO_CREATE}. {detail}")
    if status == 404:
        return f"Typeform: not found (404){' — ' + str(detail) if detail else ''}."
    return f"Typeform refused the request (HTTP {status}): {detail}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """The "test connection" probe: `GET /forms?page_size=1`, with no side
    effect, on the configured region's host. Covers `auth` and the `forms:read` scope,
    the one without which no response is readable; a 401/403 raises
    `UpstreamHTTPError`, which the probe's classification reads by its code."""
    from oto.tools.typeform import TypeformClient

    TypeformClient(access_token=fields["key"],
                   region=_region(fields.get("region"))).list_forms(page_size=1)


def _client() -> TypeformClient:
    """The Typeform client for THIS caller's credential, on its region's
    host. Real import in the body: tests replace the client, and the
    version probe reads the return annotation."""
    from oto.tools.typeform import TypeformClient

    fields = access.resolve_credential_fields("typeform")
    return TypeformClient(access_token=fields["key"],
                          region=_region(fields.get("region")))


def _run(fn, what: str = "") -> Any:
    """4xx → named refusal; 429 and 5xx stay what they are (retryable)."""
    from oto.tools.common import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        if 400 <= e.status_code < 500 and e.status_code != 429:
            raise _bad(upstream_message(e, what)) from None
        raise


# ---------------------------------------------------------------------------
# Tightened views
# ---------------------------------------------------------------------------

def _slim_workspace(ws: dict) -> dict:
    forms = ws.get("forms")
    if isinstance(forms, list):          # the reference's example makes it a list
        forms = forms[0] if forms else {}
    out = {"id": ws.get("id"), "name": ws.get("name"), "shared": ws.get("shared"),
           "forms_count": (forms or {}).get("count") if isinstance(forms, dict) else None,
           "account_id": ws.get("account_id")}
    return {k: v for k, v in out.items() if v is not None}


def _flat_fields(fields: Any) -> List[dict]:
    """All the questions, group sub-questions included (a response
    points to the sub-question, not the group)."""
    out: List[dict] = []
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        out.append(f)
        sub = (f.get("properties") or {}).get("fields")
        if isinstance(sub, list):
            out.extend(_flat_fields(sub))
    return out


def _labels(form: Optional[dict]) -> Dict[str, str]:
    """Question id → readable key: the title; two equal titles are
    told apart by the id, never merged (one would overwrite the other)."""
    if not form:
        return {}
    flat = [f for f in _flat_fields(form.get("fields")) if f.get("id")]
    titles = [str(f.get("title") or "").strip() for f in flat]
    labels = {}
    for f, title in zip(flat, titles):
        if not title:
            labels[f["id"]] = f.get("ref") or f["id"]
        elif titles.count(title) > 1:
            labels[f["id"]] = f"{title} [{f['id']}]"
        else:
            labels[f["id"]] = title
    return labels


def answer_value(answer: dict) -> Any:
    """A response's value, whatever its type: filed under the key that
    bears the name of its `type` (`text`, `choice`, `choices`, `number`…). A
    choice returns its label, a multiple choice the list of labels; an
    unknown type returns its payload as-is rather than nothing."""
    kind = answer.get("type")
    value = answer.get(kind) if kind else None
    if kind == "choice" and isinstance(value, dict):
        return value.get("label") if value.get("label") is not None else value.get("other")
    if kind == "choices" and isinstance(value, dict):
        labels = list(value.get("labels") or [])
        if value.get("other"):
            labels.append(value["other"])
        return labels
    if value is None:
        return {k: v for k, v in answer.items() if k not in ("field", "type")} or None
    return value


def _shape_response(item: dict, labels: Dict[str, str]) -> dict:
    answers: Dict[str, Any] = {}
    for a in item.get("answers") or []:
        if not isinstance(a, dict):
            continue
        field = a.get("field") or {}
        fid = field.get("id")
        key = labels.get(fid) or field.get("ref") or fid or "?"
        answers[key] = answer_value(a)
    out: Dict[str, Any] = {"response_id": item.get("response_id"),
                           "submitted_at": item.get("submitted_at"),
                           "landed_at": item.get("landed_at"),
                           "answers": answers}
    if item.get("hidden"):
        out["hidden"] = item["hidden"]
    score = (item.get("calculated") or {}).get("score")
    if score:
        out["score"] = score
    variables = {v.get("key"): v.get(v.get("type")) for v in item.get("variables") or []
                 if isinstance(v, dict) and v.get("key")}
    if variables:
        out["variables"] = variables
    return {k: v for k, v in out.items() if v is not None}


def _host(url: Any) -> Optional[str]:
    return urlsplit(url).hostname if isinstance(url, str) and url else None


def region_mismatch(form: dict, client: Any) -> Optional[str]:
    """The definition says where the responses live (`_links.responses`). A different
    host than the client's = responses that would come back empty, a deletion
    that would do nothing: the reason, or None when the hosts agree."""
    from oto.tools.typeform import REGIONS

    expected = _host((form.get("_links") or {}).get("responses"))
    current = _host(getattr(client, "BASE_URL", None))
    if not expected or not current or expected == current:
        return None
    right = {_host(u): r for r, u in REGIONS.items()}.get(expected)
    return (f"Typeform: this form's responses are stored on {expected}, but the "
            f"connector reads {current} — its responses would come back EMPTY. "
            + (f"Set the Typeform connector's data center to « {right} »."
               if right else "This data center is not one the connector knows."))


def _check_region(form: dict, client: Any) -> None:
    reason = region_mismatch(form, client)
    if reason:
        raise _bad(reason)


def register(mcp: FastMCP) -> None:
    connector_verify.register("typeform", _verify)

    @mcp.tool(annotations=LECTURE)
    def typeform_workspaces(
        search: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        full: bool = False,
    ) -> dict:
        """Typeform workspaces the token can access (read only), across
        organizations — each with its id (to filter `typeform_forms`) and its
        number of forms.

        Returns `{total_items, page_count, page, workspaces: [{id, name, shared,
        forms_count, account_id}]}`; `full=True` returns Typeform's raw page.

        Args:
            search: only workspaces whose name contains this text.
            page: 1-based page number (default 1).
            page_size: 1-200, default 10.
            full: raw payload.
        """
        res = _run(lambda: _client().list_workspaces(
            search=search, page=page, page_size=page_size), "workspaces:read")
        if full:
            return res
        return {"total_items": res.get("total_items"), "page_count": res.get("page_count"),
                "page": page or 1,
                "workspaces": [_slim_workspace(w) for w in res.get("items") or []
                               if isinstance(w, dict)]}

    @mcp.tool(annotations=LECTURE)
    def typeform_responses(
        form_id: str,
        page_size: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        response_type: Optional[List[Literal["completed", "partial", "started"]]] = None,
        sort: Optional[str] = None,
        query: Optional[str] = None,
        included_response_ids: Optional[List[str]] = None,
        excluded_response_ids: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        answered_fields: Optional[List[str]] = None,
        titles: bool = True,
        full: bool = False,
    ) -> dict:
        """Responses to a Typeform form (read only), newest first.

        Each response is returned readable: `{response_id, submitted_at,
        landed_at, answers: {<question title>: value}, hidden?, score?,
        variables?}` — a choice gives its label, a multiple choice the list of
        labels. Two questions with the same title are told apart by their id.
        Titles come from the form definition, read once per call (scope
        `forms:read`); `titles=False` skips it and keys answers by field ref.
        Metadata (browser, referer) is left out; `full=True` returns the raw page.

        To page, pass `next_before` (given when the page is full, in the
        default order) as `before` to get the next, older page. `total_items`
        is the number of matching responses. Responses from the last ~30 minutes
        may not be listed yet.

        Args:
            form_id: the form id (`typeform_forms`).
            page_size: 1-200, default 25.
            since: submitted on or after — ISO 8601 UTC to the second
                (`2026-09-01T00:00:00`) or Unix seconds.
            until: submitted on or before, same formats.
            before: cursor — responses older than this one (`next_before`).
            after: cursor — responses newer than this one (a response token).
            response_type: completed (default) | partial | started, one or
                several. Also sets the date `since`/`until` filter on:
                submission, last save, or landing.
            sort: `<field>,<asc|desc>`, default `submitted_at,desc`.
            query: exact phrase searched in answers, hidden fields, variables.
            included_response_ids: only these response ids.
            excluded_response_ids: all but these response ids.
            fields: question ids — only these answers are returned.
            answered_fields: question ids — only responses answering one of them.
            titles: key answers by question title (default) rather than ref.
            full: raw payload.
        """
        size = RESPONSES_DEFAULT_PAGE if page_size is None else page_size
        if not 1 <= size <= RESPONSES_MAX_PAGE:
            raise _bad(f"`page_size` must be between 1 and {RESPONSES_MAX_PAGE} "
                       f"(got {page_size}); page with `before`.")
        if before and after:
            raise _bad("pass `before` OR `after`, not both.")
        client = _client()
        form = None
        if titles and not full:
            form = _run(lambda: client.get_form(form_id), "forms:read")
            _check_region(form, client)
        res = _run(lambda: client.list_responses(
            form_id, page_size=size, since=since, until=until, after=after,
            before=before, included_response_ids=included_response_ids,
            excluded_response_ids=excluded_response_ids,
            response_type=response_type, sort=sort, query=query, fields=fields,
            answered_fields=answered_fields), "responses:read")
        if full:
            return res
        items = [i for i in res.get("items") or [] if isinstance(i, dict)]
        labels = _labels(form)
        out: Dict[str, Any] = {
            "form_id": form_id, "total_items": res.get("total_items"),
            "responses": [_shape_response(i, labels) for i in items],
            "omitted": list(RESPONSE_OMITTED)}
        if form:
            out["form_title"] = form.get("title")
        # The next page's cursor: the last item's token, as long as
        # the page is full and the order is the default one (most recent
        # first) — it is the traversal the reference describes.
        if items and len(items) == size and not after and not sort:
            out["next_before"] = items[-1].get("token")
        return out

    @mcp.tool(annotations=LECTURE)
    def typeform_responses_summary(
        form_id: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        response_type: Optional[List[Literal["completed", "partial", "started"]]] = None,
        max_pages: int = 10,
    ) -> dict:
        """Statistics of a Typeform form's responses, computed by oto from the
        form and its responses (no model; no text answer is read, only counted).

        Returns `{form_id, form_title, filters, total_items, responses_analyzed,
        pages_read, max_pages, truncated, note?, responses_per_day: [{date,
        count}], score?, fields: [{id, ref, title, type, answered, answer_rate,
        choices?: [{label, count, share}], ranking?, numbers?: {count, mean,
        min, max}, distribution?, nps?: {promoters, passives, detractors,
        score}, booleans?}]}`. Reads up to `max_pages` pages of 1000 responses,
        newest first: `truncated: true` means the oldest ones were not counted
        — narrow `since`/`until` or raise `max_pages`. Scopes forms:read and
        responses:read.

        Args:
            form_id: the form id (`typeform_forms`).
            since: on or after — ISO 8601 UTC to the second or Unix seconds.
            until: on or before, same formats.
            response_type: completed (default) | partial | started; also picks
                the timestamp the dates filter on.
            max_pages: pages of 1000 read at most, 1-50 (default 10).
        """
        client = _client()
        form = _run(lambda: client.get_form(form_id), "forms:read")
        _check_region(form, client)
        return _run(lambda: client.summarize_responses(
            form_id, since=since, until=until, response_type=response_type,
            max_pages=max_pages), "forms:read, responses:read")

    @mcp.tool()
    def typeform_delete_responses(
        form_id: str,
        response_ids: List[str],
        confirm: bool = False,
    ) -> dict:
        """⚠️ Delete responses of a Typeform form, IRREVERSIBLY (up to 1000).

        Two steps. Without `confirm=True`, nothing is deleted: returns `{dry_run,
        would_delete: {form_id, form_title, found: [{response_id, submitted_at,
        landed_at}], not_found: [ids]}, note}` — Typeform silently ignores an
        id that matches no response. With `confirm=True`: `{deletion_registered,
        form_id, response_ids, note}`; the deletion is asynchronous (registered,
        not done yet): check later with `typeform_responses(included_response_ids=…)`.
        Refused when the form's responses live in another data center (the
        deletion would do nothing). Scopes responses:write, plus forms:read and
        responses:read for the preview.

        Args:
            form_id: the form id.
            response_ids: `response_id` of each response to delete (1-1000).
            confirm: True to delete, after reading the preview.
        """
        ids = list(dict.fromkeys(response_ids or []))
        if not 1 <= len(ids) <= DELETE_MAX_IDS:
            raise _bad(f"`response_ids` takes 1 to {DELETE_MAX_IDS} ids (got {len(ids)}).")
        client = _client()
        form = _run(lambda: client.get_form(form_id), "forms:read")
        _check_region(form, client)
        if not confirm:
            page = _run(lambda: client.list_responses(
                form_id, page_size=len(ids), included_response_ids=ids,
                response_type=ALL_RESPONSE_TYPES), "responses:read")
            found = [{"response_id": i.get("response_id"),
                      "submitted_at": i.get("submitted_at"),
                      "landed_at": i.get("landed_at")}
                     for i in page.get("items") or [] if isinstance(i, dict)]
            seen = {f["response_id"] for f in found}
            return _preview("would_delete", {
                "form_id": form_id, "form_title": form.get("title"), "found": found,
                "not_found": [i for i in ids if i not in seen]},
                "Deleted responses cannot be recovered.")
        _run(lambda: client.delete_responses(form_id, ids), "responses:write")
        return {"deletion_registered": True, "form_id": form_id, "response_ids": ids,
                "note": "Asynchronous: Typeform registered the deletion; it may take "
                        "a moment. Ids matching no response were ignored."}
