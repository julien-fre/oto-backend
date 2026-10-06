"""Granola — meeting notes, transcripts, folders, webhook endpoints.

Wraps `oto.tools.granola.client.GranolaClient` (API v1, Bearer). keyed
`api_key`, byo-only (no platform key): each user/org sets THEIR Granola
key — two kinds of key on Granola's side (personal, self-serve; workspace,
provisioned by an admin), both a simple Bearer here, Granola applies
the scope itself.

Small API (8 operations, 4 objects), grouped into **TWO tools by category**:
- `granola_content` — everything that READS meeting content (notes, transcript,
  folders), verb in `op`.
- `granola_webhook_endpoint` — integration management (webhook endpoints),
  verb in `op`, full CRUD.

**No param is silently dropped**: an `op` that does not recognize a
supplied argument REFUSES rather than ignoring it (silae `_refuse_ignored`) —
`granola_content(op="list_notes", note_id=...)` would return ALL notes while
letting the caller believe `note_id` filtered down to one.

**Webhook endpoints: full CRUD exposed**, unlike Ahrefs' Management
guide (read+create only) — deleting/disabling a webhook endpoint is not
destructive of USER DATA (notes, transcripts…), it is integration plumbing,
small blast radius.

**Verified against the real OpenAPI 3.1.0 spec** (`docs.granola.ai/api-reference/
openapi.json`, 2026-08-20), not against a doc page summary — required,
body shapes, `page_size` bounds. **Tested live on 2026-08-20** with a real
workspace key: notes, transcript, folders, and the full
create→update→delete cycle of a webhook endpoint respond
exactly as coded, including the 400 validation errors (`page_size`
out of bounds, invalid `note_id`, `scopes` incompatible with a workspace key
— confirmed: a workspace key MUST pass `scopes=["workspace"]`, exactly
as documented). The spec's 9th endpoint, `GET /v1/audit`, returned
`404 NOT_FOUND` on this key (probably plan-gated) — removed rather than
exposing a call that nobody can currently use (cf. oto-core
`GranolaClient`).
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """A supplied argument that THIS op does not use is an error of intent,
    not a detail — otherwise `granola_content(op="get_note", folder_id=...)`
    would return ONE note while letting the caller believe `folder_id` filtered something."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Granola rejected the API key (HTTP {status}) — check the key set on this "
                "connector (Granola: Settings → Connectors → API keys).")
    if status == 404:
        return f"Granola: resource not found (HTTP 404) — {e.body}"
    if status == 429:
        return ("Granola: too many requests (429) — limit 25 req/5s burst, "
                "5 req/s (300/min) sustained. Try again in a moment.")
    if status in (500, 502, 503, 504):
        return f"Granola is temporarily unavailable (HTTP {status}) — try again later."
    return f"Granola refused the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Probe for "test the connection": list of webhook endpoints, no param,
    free (no unit limit documented on Granola's side)."""
    from oto.tools.granola.client import GranolaClient
    GranolaClient(api_key=fields["key"]).list_webhook_endpoints()


def register(mcp: FastMCP) -> None:
    from oto.tools.granola.client import GranolaClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("granola", _verify)

    def _client() -> GranolaClient:
        key, _ = access.resolve_api_key("granola")
        return GranolaClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Content — notes, transcripts, folders (read-only)
    # ================================================================

    @mcp.tool()
    def granola_content(
        op: Literal["list_notes", "get_note", "get_transcript", "list_folders"] = "list_notes",
        note_id: Optional[str] = None,
        include: Optional[Literal["transcript"]] = None,
        created_before: Optional[str] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        folder_id: Optional[str] = None,
        cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> object:
        """Meeting content — notes, transcripts, and folders.

        Args:
            op: "list_notes" (default) | "get_note" | "get_transcript" | "list_folders".
            note_id: REQUIRED by "get_note"/"get_transcript" — the note id
                (`not_...`). Refused on "list_notes"/"list_folders", which
                enumerate everything reachable and would ignore it.
            include: op="get_note" only — "transcript" inlines the transcript
                in the response; may return `TRANSCRIPT_TOO_LARGE` for long
                meetings, use op="get_transcript" then (paginated from the start).
            created_before/created_after/updated_after: op="list_notes"
                filters — date (YYYY-MM-DD) or date-time (ISO 8601).
            folder_id: op="list_notes" — restrict to this folder and its
                subfolders (from op="list_folders").
            cursor: pagination cursor from a previous response — valid on
                "list_notes"/"get_transcript"/"list_folders".
            page_size: "list_notes"/"list_folders" — 1-30, default 10.
                "get_transcript" — 1-100, default 50.
        """
        client = _client()
        if op == "list_notes":
            _refuse_ignored(op, "use op='get_note' for a specific note",
                             note_id=note_id, include=include)
            params = {k: v for k, v in dict(
                created_before=created_before, created_after=created_after,
                updated_after=updated_after, folder_id=folder_id, cursor=cursor,
                page_size=page_size).items() if v is not None}
            return _run(lambda: client.list_notes(**params))
        if op == "get_note":
            if not note_id:
                raise _bad("op='get_note' requires `note_id`")
            _refuse_ignored(op, "these filters only apply to op='list_notes'/'list_folders'",
                             created_before=created_before, created_after=created_after,
                             updated_after=updated_after, folder_id=folder_id,
                             cursor=cursor, page_size=page_size)
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.get_note(note_id, **kwargs))
        if op == "get_transcript":
            if not note_id:
                raise _bad("op='get_transcript' requires `note_id`")
            _refuse_ignored(op, "these filters do not apply to op='get_transcript'",
                             include=include, created_before=created_before,
                             created_after=created_after, updated_after=updated_after,
                             folder_id=folder_id)
            kwargs = {k: v for k, v in dict(cursor=cursor, page_size=page_size).items()
                      if v is not None}
            return _run(lambda: client.get_transcript(note_id, **kwargs))
        if op == "list_folders":
            _refuse_ignored(op, "use op='list_notes' to filter notes",
                             note_id=note_id, include=include, created_before=created_before,
                             created_after=created_after, updated_after=updated_after,
                             folder_id=folder_id)
            kwargs = {k: v for k, v in dict(cursor=cursor, page_size=page_size).items()
                      if v is not None}
            return _run(lambda: client.list_folders(**kwargs))
        raise _bad("op must be 'list_notes', 'get_note', 'get_transcript' or 'list_folders'")

    # ================================================================
    # Webhook endpoints — integration management (CRUD)
    # ================================================================

    @mcp.tool()
    def granola_webhook_endpoint(
        op: Literal["list", "create", "update", "delete"] = "list",
        webhook_endpoint_id: Optional[str] = None,
        url: Optional[str] = None,
        scopes: Optional[List[Literal["personal", "public", "workspace"]]] = None,
        events: Optional[List[Literal["note.access_granted", "note.edited", "note.generated"]]] = None,
        folder_ids: Optional[List[str]] = None,
        enabled: Optional[bool] = None,
    ) -> object:
        """A webhook endpoint receiving Granola event deliveries — list,
        create, update, or delete one. Unlike most write-capable tools here,
        the full CRUD lifecycle is exposed: managing a webhook subscription
        isn't destructive of user data (notes/transcripts), just integration
        plumbing.

        Args:
            op: "list" (default) | "create" | "update" | "delete".
            webhook_endpoint_id: REQUIRED by "update"/"delete" (`whe_...`).
                Refused on "list"/"create".
            url: REQUIRED by "create" — publicly reachable HTTPS URL to
                deliver events to. Optional on "update" (change it).
            scopes: REQUIRED by "create" — which notes to receive events for:
                `["personal"]` (notes you own/shared with you), `["public"]`
                (workspace-visible notes), or both. A Workspace API key must
                pass exactly `["workspace"]` (confirmed live). Optional on "update".
            events: which event types to subscribe to (omit on "create" = all
                three). Optional on "update".
            folder_ids: restrict delivery to these folders + their subfolders
                (from `granola_content(op="list_folders")`, max 100). Omit =
                every note matching `scopes`. Optional on "update".
            enabled: "update" only — pause/resume delivery without deleting
                the endpoint.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "use op='create' to register a new one, "
                             "op='update'/'delete' to target an existing one",
                             webhook_endpoint_id=webhook_endpoint_id, url=url, scopes=scopes,
                             events=events, folder_ids=folder_ids, enabled=enabled)
            return _run(lambda: client.list_webhook_endpoints())
        if op == "create":
            _refuse_ignored(op, "a new endpoint has no id yet, nor an enabled state",
                             webhook_endpoint_id=webhook_endpoint_id, enabled=enabled)
            if not url or not scopes:
                raise _bad("op='create' requires `url` and `scopes`")
            body = {k: v for k, v in dict(events=events, folder_ids=folder_ids).items()
                    if v is not None}
            return _run(lambda: client.create_webhook_endpoint(url, scopes, **body))
        if op in ("update", "delete"):
            if not webhook_endpoint_id:
                raise _bad(f"op={op!r} requires `webhook_endpoint_id`")
            if op == "delete":
                _refuse_ignored(op, "a deletion takes no other field",
                                 url=url, scopes=scopes, events=events,
                                 folder_ids=folder_ids, enabled=enabled)
                return _run(lambda: client.delete_webhook_endpoint(webhook_endpoint_id))
            body = {k: v for k, v in dict(url=url, scopes=scopes, events=events,
                                           folder_ids=folder_ids, enabled=enabled).items()
                    if v is not None}
            if not body:
                raise _bad("op='update' requires at least one field to change")
            return _run(lambda: client.update_webhook_endpoint(webhook_endpoint_id, **body))
        raise _bad("op must be 'list', 'create', 'update' or 'delete'")
