"""Typeform — the forms: `typeform_forms`, read and written.

Second module of the connector (`typeform` holds the shared base and the
responses, `typeform_webhooks` the webhooks). One tool, verb in `op`:
`list`/`get` read, `create`/`update` write, `replace`/`delete` destroy.

**What the tool layer adds to the transport**:
1. One carrier for a definition, `definition`, in the very shape `op="get",
   full=True` returns — so "read, change, send back" needs no remapping. The
   keys Typeform sets itself (`id`, `_links`, timestamps) are not sent and the
   response NAMES them (`not_sent`); any other unknown key is REFUSED, never
   dropped.
2. **`replace` and `delete` take two steps.** Without `confirm=True` they return a
   preview read from Typeform: for `replace`, the fields that would disappear
   (with their answers) and those that would be created; for `delete`, the form
   and its number of completed responses. A field is matched by its `id`.
3. **Publication is said, not guessed**: a created form is public by default
   upstream, so `create` returns `is_public` and, when true, says the form is live.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from .typeform import (_bad, _client, _flat_fields, _preview, _refuse_ignored, _run,
                       region_mismatch, upstream_message)

#: What a form's tightened view removes (returned with `full=True`).
FORM_OMITTED = ("welcome_screens", "thankyou_screens", "logic", "theme",
                "settings", "attachments/layouts")

#: The keys of a definition, as `create_form`/`replace_form` take them.
DEFINITION_KEYS = ("title", "type", "settings", "theme", "workspace", "hidden",
                   "variables", "welcome_screens", "thankyou_screens", "fields", "logic")

#: Keys `get` returns that Typeform sets itself: never written, named when skipped.
SERVER_KEYS = frozenset({"id", "_links", "self", "created_at", "last_updated_at",
                         "published_at"})


def _slim_form_row(f: dict) -> dict:
    out = {"id": f.get("id"), "title": f.get("title"),
           "last_updated_at": f.get("last_updated_at"),
           "created_at": f.get("created_at"),
           "is_public": (f.get("settings") or {}).get("is_public"),
           "url": (f.get("_links") or {}).get("display")}
    return {k: v for k, v in out.items() if v is not None}


def _slim_field(field: dict) -> dict:
    """A question: enough to recognize it in a response (id, ref) and
    interpret it (type, title, choices). Groups keep their sub-questions."""
    props = field.get("properties") or {}
    out: Dict[str, Any] = {"id": field.get("id"), "ref": field.get("ref"),
                           "type": field.get("type"), "title": field.get("title")}
    if props.get("description"):
        out["description"] = props["description"]
    if (field.get("validations") or {}).get("required"):
        out["required"] = True
    choices = props.get("choices")
    if isinstance(choices, list) and choices:
        out["choices"] = [ch.get("label") for ch in choices if isinstance(ch, dict)]
        if props.get("allow_multiple_selection"):
            out["multiple"] = True
        if props.get("allow_other_choice"):
            out["other_allowed"] = True
    sub = props.get("fields")
    if isinstance(sub, list) and sub:
        out["fields"] = [_slim_field(s) for s in sub if isinstance(s, dict)]
    return {k: v for k, v in out.items() if v is not None}


def _slim_form(form: dict) -> dict:
    links = form.get("_links") or {}
    out = {"id": form.get("id"), "title": form.get("title"),
           "language": form.get("language") or (form.get("settings") or {}).get("language"),
           "url": links.get("display"),
           "fields": [_slim_field(f) for f in form.get("fields") or [] if isinstance(f, dict)],
           "hidden": form.get("hidden") or None,
           "variables": form.get("variables") or None,
           "omitted": list(FORM_OMITTED)}
    return {k: v for k, v in out.items() if v is not None}


def _definition(op: str, definition: Any) -> tuple:
    """`definition` → (body for the client, server keys not sent). An unknown key
    is refused: it would otherwise vanish while the caller believes it written."""
    if not isinstance(definition, dict) or not definition:
        raise _bad(f"op={op!r} requires `definition`: {{title, fields?, settings?, …}}.")
    unknown = sorted(set(definition) - set(DEFINITION_KEYS) - SERVER_KEYS)
    if unknown:
        raise _bad(f"`definition`: unknown key(s) {unknown} — accepted: "
                   f"{', '.join(DEFINITION_KEYS)}.")
    title = definition.get("title")
    if not isinstance(title, str) or not title.strip():
        raise _bad("`definition.title` is required (a non-empty string).")
    body = {k: definition[k] for k in DEFINITION_KEYS if definition.get(k) is not None}
    return body, sorted(SERVER_KEYS & set(definition))


def _written(verb: str, form: Any, not_sent: List[str], full: bool) -> dict:
    """What `create`/`replace` return: the form view, its visibility said."""
    if full:
        return form
    form = form if isinstance(form, dict) else {}
    out: Dict[str, Any] = {verb: True, **_slim_form(form),
                           "is_public": (form.get("settings") or {}).get("is_public")}
    if out["is_public"]:
        out["note"] = ("The form is PUBLIC: anyone with its url can answer. Unpublish "
                       "with op='update', operations=[{op: replace, path: "
                       "/settings/is_public, value: false}].")
    if not_sent:
        out["not_sent"] = not_sent
    return out


def _replace_preview(form_id: str, current: dict, body: dict, not_sent: List[str]) -> dict:
    """What a PUT would change, read against the current form: a field whose id
    is absent from the new definition is deleted with its answers."""
    before = {f["id"]: f for f in _flat_fields(current.get("fields")) if f.get("id")}
    after = _flat_fields(body.get("fields"))
    kept = {f.get("id") for f in after} & set(before)
    what: Dict[str, Any] = {
        "form_id": form_id,
        "title": ({"from": current.get("title"), "to": body["title"]}
                  if current.get("title") != body["title"] else body["title"]),
        "fields_kept": len(kept),
        "fields_removed": [{"id": i, "title": f.get("title")}
                           for i, f in before.items() if i not in kept],
        "fields_added": [f.get("title") for f in after if f.get("id") not in before],
    }
    if "theme" not in body:
        what["theme"] = "not sent: Typeform applies a new copy of its default theme"
    if "settings" not in body:
        what["settings"] = ("not sent: the form may fall back to Typeform's default "
                            "settings, which make it public")
    if not_sent:
        what["not_sent"] = not_sent
    return _preview("would_replace", what,
                    "A removed field is deleted with its answers in every response, "
                    "irreversibly.")


def _completed_responses(client: Any, form: dict, form_id: str) -> Dict[str, Any]:
    """How many completed responses a deletion takes with it — or why it cannot
    be counted (another data center would count zero; a missing scope)."""
    from oto.tools.common import UpstreamHTTPError

    reason = region_mismatch(form, client)
    if reason:
        return {"completed_responses": None, "count_unavailable": reason}
    try:
        page = client.list_responses(form_id, page_size=1)
    except UpstreamHTTPError as e:
        if e.status_code not in (401, 403):
            raise
        return {"completed_responses": None,
                "count_unavailable": upstream_message(e, "responses:read")}
    return {"completed_responses": (page or {}).get("total_items")}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def typeform_forms(
        op: Literal["list", "get", "create", "update", "replace", "delete"] = "list",
        form_id: Optional[str] = None,
        search: Optional[str] = None,
        workspace_id: Optional[str] = None,
        sort_by: Optional[Literal["created_at", "last_updated_at"]] = None,
        order_by: Optional[Literal["asc", "desc"]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        definition: Optional[Dict[str, Any]] = None,
        operations: Optional[List[Dict[str, Any]]] = None,
        confirm: bool = False,
        full: bool = False,
    ) -> dict:
        """Typeform forms: list, read, create, edit, publish, delete.

        `op`:
        - `list` — forms of the account, public and private: `{total_items,
          page_count, page, forms: [{id, title, last_updated_at, created_at,
          is_public, url}]}`.
        - `get` — one form (`form_id`): its questions `fields: [{id, ref, type,
          title, description?, required?, choices?, multiple?, fields? (group)}]`,
          `hidden` field names and `variables` — what you need to read its
          responses. Screens, logic, theme and settings are left out
          (`omitted`); `full=True` returns the whole definition, the shape
          `definition` takes.
        - `create` — a form from `definition`. ⚠️ It is PUBLIC (live at its
          `url`) unless `definition.settings.is_public` is false. Returns the
          form view with `is_public`.
        - `update` — JSON Patch `operations` on `form_id`, fields untouched:
          `[{"op": "replace", "path": "/settings/is_public", "value": true}]`
          publishes (false unpublishes); also `/title`, `/theme`, `/workspace`
          (`{href}`), `/settings/meta`.
        - `replace` — ⚠️ overwrites the WHOLE form with `definition`: a field
          left out is deleted with its answers in every response. Start from
          `op="get", full=True` and keep each field's `id`.
        - `delete` — ⚠️ deletes the form AND all its responses. To stop
          collecting but keep them, unpublish with `update`.

        `replace` and `delete` are irreversible: without `confirm=True` they
        only return a preview (`dry_run`: fields removed, responses at stake).
        Scopes forms:read to read, forms:write to write.

        Args:
            op: list | get | create | update | replace | delete.
            form_id: get / update / replace / delete — the form id (last
                segment of its URL, e.g. `u6nXL7` in `…typeform.com/to/u6nXL7`).
            search: list — only forms containing this text.
            workspace_id: list — only this workspace's forms
                (`typeform_workspaces`).
            sort_by: list — created_at | last_updated_at.
            order_by: list — asc | desc.
            page: list — 1-based page number (default 1).
            page_size: list — 1-200, default 10.
            definition: create / replace — `{title, type?, fields?: [{title,
                type, id?, ref?, properties?, validations?}], settings?, theme?:
                {href}, workspace?: {href}, hidden?, variables?,
                welcome_screens?, thankyou_screens?, logic?}`.
            operations: update — `[{op: "replace", path, value}]`.
            confirm: replace / delete — True to write, after the preview.
            full: list / get / create / replace — raw payload.
        """
        reading = dict(search=search, workspace_id=workspace_id, sort_by=sort_by,
                       order_by=order_by, page=page, page_size=page_size)
        if op not in ("replace", "delete"):
            _refuse_ignored(op, "it only applies to op='replace' or op='delete'",
                            confirm=confirm or None)
        if op != "list":
            if not form_id and op != "create":
                raise _bad(f"op={op!r}: `form_id` is required.")
            _refuse_ignored(op, "it only applies to op='list'", **reading)
        if op not in ("create", "replace"):
            _refuse_ignored(op, "it only applies to op='create' or op='replace'",
                            definition=definition)
        if op != "update":
            _refuse_ignored(op, "it only applies to op='update'", operations=operations)

        if op == "list":
            _refuse_ignored(op, "use op='get' to read one form", form_id=form_id)
            res = _run(lambda: _client().list_forms(
                search=search, page=page, page_size=page_size,
                workspace_id=workspace_id, sort_by=sort_by, order_by=order_by),
                "forms:read")
            if full:
                return res
            return {"total_items": res.get("total_items"),
                    "page_count": res.get("page_count"), "page": page or 1,
                    "forms": [_slim_form_row(f) for f in res.get("items") or []
                              if isinstance(f, dict)]}
        if op == "get":
            form = _run(lambda: _client().get_form(form_id), "forms:read")
            return form if full else _slim_form(form)
        if op == "create":
            _refuse_ignored(op, "a created form gets its id from Typeform",
                            form_id=form_id)
            body, not_sent = _definition(op, definition)
            created = _run(lambda: _client().create_form(**body), "forms:write")
            return _written("created", created, not_sent, full)
        if op == "update":
            _refuse_ignored(op, "it returns no form", full=full or None)
            if not operations:
                raise _bad("op='update' requires `operations`: [{op: replace, path, value}].")
            _run(lambda: _client().update_form(form_id, operations), "forms:write")
            return {"updated": True, "form_id": form_id,
                    "paths": [o.get("path") for o in operations if isinstance(o, dict)]}
        if op == "replace":
            body, not_sent = _definition(op, definition)
            client = _client()
            if not confirm:
                _refuse_ignored(op, "the preview returns no form", full=full or None)
                current = _run(lambda: client.get_form(form_id), "forms:read")
                return _replace_preview(form_id, current, body, not_sent)
            replaced = _run(lambda: client.replace_form(form_id, **body), "forms:write")
            return _written("replaced", replaced, not_sent, full)
        if op == "delete":
            _refuse_ignored(op, "it returns no form", full=full or None)
            client = _client()
            if not confirm:
                form = _run(lambda: client.get_form(form_id), "forms:read")
                return _preview("would_delete", {
                    "form_id": form_id, "title": form.get("title"),
                    "url": (form.get("_links") or {}).get("display"),
                    "fields": len(_flat_fields(form.get("fields"))),
                    **_run(lambda: _completed_responses(client, form, form_id),
                           "responses:read")},
                    "The form and ALL its responses are deleted, irreversibly.")
            _run(lambda: client.delete_form(form_id), "forms:write")
            return {"deleted": True, "form_id": form_id}
        raise _bad(f"invalid `op`: {op!r} (expected: list | get | create | update | "
                   "replace | delete).")
