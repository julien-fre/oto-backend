"""Grain — meeting recordings, transcripts, sharing, webhooks, workspace org data.

Wraps `oto.tools.grain.client.GrainClient` (API v2, Bearer +
`Public-Api-Version`). keyed `api_key`, byo-only (no platform key):
each user/org sets THEIR Grain key — Personal Access Token (per user) or
Workspace Access Token (admin, "access to ALL the workspace's data"),
both a simple Bearer here, Grain applies the scope itself.

**5 tools, one per business object** (silae, ADR 0047) — Grain has more distinct
objects than Granola, so no merge into 2 as for the latter:
- `grain_recording` — CRUD + sharing of a meeting (op=list/get/update/tag/
  untag/share_user/unshare_user/share_team/unshare_team).
- `grain_transcript` — the 4 transcript formats (json/txt/vtt/srt), separate
  because the nature of the response (plain text vs JSON) differs radically
  from the rest of the tools here.
- `grain_recording_file` — download + getting an upload URL, separate
  because these are raw bytes, not JSON (op=download/create_upload_url).
- `grain_hook` — full CRUD of webhooks (op=list/create/delete) — not
  destructive of USER DATA, just integration plumbing
  (same stance as `granola_webhook_endpoint`).
- `grain_org` — the 3 unfiltered lists (users/teams/meeting_types),
  merged into a single tool since they are so trivial (op=users/teams/
  meeting_types).

**No param is silently dropped**: an `op` that does not recognize a
supplied argument REFUSES rather than ignoring it (silae `_refuse_ignored`).

⚠️ **No machine-readable spec exists for this API** (openapi.json/
docs.json/mint.json/llms.txt all return 403, a constant WAF block, not
a 404 of absence) — everything here first comes from reading doc pages.

**Tested live on 2026-08-20** with a real Personal Access Token
(a customer workspace): 20 of the 21 methods worked first time —
list/get recordings, the 4 transcript formats, tag/untag, share/unshare
user, update (rename), download (21 MB real), create_upload_url (a
real pre-signed S3 URL — confirms the choice of NOT sending the Grain
Bearer to it), and the full hook cycle (create against a real reachable
URL, list, delete). **One real bug found and fixed**: `share_with_team`
expected `team_id` in the JSON body (plural PUT `.../teams`), not in
the path as the doc suggested — see `GrainClient.share_with_team`
for details. Exactly the class of bug
that Ahrefs' OpenAPI verification had caught and that a doc-only search
had missed here too.

⚠️ **A Personal Access Token IS NOT scoped to the holder's meetings.**
Verified live: `grain_recording(op="list")` without a filter returns the
`share_state="public"` recordings of the WHOLE organization (seen in the
test: meetings recorded by other workspace members, where the
token holder was not even a participant) — the PAT gives access to
personal notes (owned/shared) **AND** to the workspace's public notes,
not only its own. To scope to "my meetings":
`filter={"attendance": "hosted"}` (hosted by the holder) or `"attended"`
(that they took part in) — confirmed live, these two values filter
correctly. Without this filter, an agent that lists carelessly can surface
a colleague's client calls.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """A supplied argument that THIS op does not use is an error of intent,
    not a detail — otherwise `grain_recording(op="list", recording_id=...)`
    would return ALL meetings while letting the caller believe `recording_id`
    filtered down to one."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Grain rejected the API key (HTTP {status}) — check the key set on this "
                "connector (Grain: Settings → Integrations → API).")
    if status == 404:
        return f"Grain: resource not found (HTTP 404) — {e.body}"
    if status == 429:
        return ("Grain: too many requests (429) — limit 300/minute. "
                "Try again in a moment.")
    if status in (500, 502, 503, 504):
        return f"Grain is temporarily unavailable (HTTP {status}) — try again later."
    return f"Grain refused the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Probe for "test the connection": list of meeting types, unfiltered,
    the lightest of the 3 org lists."""
    from oto.tools.grain.client import GrainClient
    GrainClient(api_key=fields["key"]).list_meeting_types()


def register(mcp: FastMCP) -> None:
    from oto.tools.grain.client import GrainClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("grain", _verify)

    def _client() -> GrainClient:
        key, _ = access.resolve_api_key("grain")
        return GrainClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Recordings — CRUD + sharing
    # ================================================================

    @mcp.tool()
    def grain_recording(
        op: Literal["list", "get", "update", "tag", "untag",
                     "share_user", "unshare_user", "share_team", "unshare_team"] = "list",
        recording_id: Optional[str] = None,
        cursor: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        include: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        tag: Optional[str] = None,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> object:
        """A meeting recording — list, fetch, rename, tag, or share one.

        ⚠️ op="list" WITHOUT `filter.attendance` returns every workspace-public
        recording, not just the caller's — confirmed live (2026-08-20): a
        Personal Access Token sees its owner's own/shared notes AND every
        `share_state="public"` recording made by anyone else in the org, even
        ones the token owner never attended. Pass `filter={"attendance":
        "hosted"}` (recordings the caller ran) or `"attended"` (recordings
        they were in) to scope to "my meetings only" — omit it and an agent
        can surface a colleague's client call.

        Args:
            op: "list" (default) | "get" | "update" | "tag" | "untag" |
                "share_user" | "unshare_user" | "share_team" | "unshare_team".
            recording_id: REQUIRED by every op except "list". Refused on "list".
            cursor: op="list" only — pagination cursor from a previous response.
            filter: op="list" only — {"before_datetime"?, "after_datetime"?,
                "attendance"? ("hosted"|"attended", Personal-key only — see
                the warning above),
                "participant_scope"? ("internal"|"external"), "title_search"?,
                "team"? (uuid), "meeting_type"? (uuid)}.
            include: "list"/"get" — {"highlights"?, "participants"?,
                "ai_action_items"?, "ai_summary"?, "private_notes"?
                (Personal-key only), "calendar_event"?, "hubspot"?,
                "screenshares"?, "ai_template_sections"?: {"format": "json"|
                "markdown"|"text"}}. Highlights are NOT a separate tool — set
                `include={"highlights": True}` to get them here. Confirmed
                live: `include={"hubspot": True}` returns
                `{"hubspot_company_ids": [...], "hubspot_deal_ids": [...]}`;
                `include={"participants": True}` items carry `hs_contact_id`
                (null if unlinked).
            title: REQUIRED by "update" — the new title.
            tag: REQUIRED by "tag"/"untag".
            user_id: REQUIRED by "share_user"/"unshare_user".
            team_id: REQUIRED by "share_team"/"unshare_team".
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "use op='get' for a specific meeting",
                             recording_id=recording_id, title=title, tag=tag,
                             user_id=user_id, team_id=team_id)
            body = {k: v for k, v in dict(cursor=cursor, filter=filter, include=include).items()
                    if v is not None}
            return _run(lambda: client.list_recordings(**body))
        if recording_id is None:
            raise _bad(f"op={op!r} requires `recording_id`")
        if op == "get":
            _refuse_ignored(op, "these filters only apply to op='list'",
                             cursor=cursor, filter=filter, title=title, tag=tag,
                             user_id=user_id, team_id=team_id)
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.get_recording(recording_id, **kwargs))
        if op == "update":
            _refuse_ignored(op, "op='update' only changes the title",
                             cursor=cursor, filter=filter, include=include, tag=tag,
                             user_id=user_id, team_id=team_id)
            if not title:
                raise _bad("op='update' requires `title`")
            return _run(lambda: client.update_recording(recording_id, title))
        if op in ("tag", "untag"):
            _refuse_ignored(op, f"op={op!r} only takes `tag`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             user_id=user_id, team_id=team_id)
            if not tag:
                raise _bad(f"op={op!r} requires `tag`")
            if op == "tag":
                return _run(lambda: client.add_tag(recording_id, tag))
            return _run(lambda: client.remove_tag(recording_id, tag))
        if op in ("share_user", "unshare_user"):
            _refuse_ignored(op, f"op={op!r} only takes `user_id`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             tag=tag, team_id=team_id)
            if not user_id:
                raise _bad(f"op={op!r} requires `user_id`")
            if op == "share_user":
                return _run(lambda: client.share_with_user(recording_id, user_id))
            return _run(lambda: client.unshare_from_user(recording_id, user_id))
        if op in ("share_team", "unshare_team"):
            _refuse_ignored(op, f"op={op!r} only takes `team_id`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             tag=tag, user_id=user_id)
            if not team_id:
                raise _bad(f"op={op!r} requires `team_id`")
            if op == "share_team":
                return _run(lambda: client.share_with_team(recording_id, team_id))
            return _run(lambda: client.unshare_from_team(recording_id, team_id))
        raise _bad("unknown op")

    # ================================================================
    # Transcript — 4 formats
    # ================================================================

    @mcp.tool()
    def grain_transcript(
        recording_id: str,
        format: Literal["json", "txt", "vtt", "srt"] = "json",  # noqa: A002
    ) -> object:
        """A recording's transcript, in the requested format.

        Args:
            recording_id: the recording id.
            format: "json" (default, structured) | "txt" (plain text) |
                "vtt" (WebVTT, for video players) | "srt" (SubRip, for video editors).
        """
        client = _client()
        if format == "json":
            return _run(lambda: client.get_transcript(recording_id))
        if format == "txt":
            return _run(lambda: client.get_transcript_text(recording_id))
        if format == "vtt":
            return _run(lambda: client.get_transcript_vtt(recording_id))
        if format == "srt":
            return _run(lambda: client.get_transcript_srt(recording_id))
        raise _bad("format must be 'json', 'txt', 'vtt' or 'srt'")

    # ================================================================
    # Recording file — upload/download (raw bytes)
    # ================================================================

    @mcp.tool()
    def grain_recording_file(
        op: Literal["download", "create_upload_url"] = "download",
        recording_id: Optional[str] = None,
        filename: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> object:
        """A recording's underlying file — download the raw bytes, or get a
        one-time URL to upload a new recording.

        Note: uploading the actual file bytes to the URL this returns is NOT
        done through MCP (binary payload, no natural fit for a JSON tool
        call) — `op="create_upload_url"` gives you the `url` to PUT the file
        to directly; `oto.tools.grain.client.GrainClient.upload_recording_file`
        is available for scripted use outside the agent loop.

        Args:
            op: "download" (default) | "create_upload_url".
            recording_id: REQUIRED by "download". Refused on "create_upload_url".
            filename: REQUIRED by "create_upload_url" — the file being uploaded
                (.mov/.mp4/.mp3/.m4a). Refused on "download".
            user_id: op="create_upload_url" with a Workspace API key only —
                who owns the resulting recording. Refused on "download".
        """
        client = _client()
        if op == "download":
            _refuse_ignored(op, "use op='create_upload_url' to upload a new one",
                             filename=filename, user_id=user_id)
            if not recording_id:
                raise _bad("op='download' requires `recording_id`")
            content = _run(lambda: client.download_recording(recording_id))
            return {"recording_id": recording_id, "size_bytes": len(content),
                    "note": "Binary content returned as bytes, not shown inline."}
        if op == "create_upload_url":
            _refuse_ignored(op, "op='create_upload_url' downloads nothing",
                             recording_id=recording_id)
            if not filename:
                raise _bad("op='create_upload_url' requires `filename`")
            kwargs = {"user_id": user_id} if user_id is not None else {}
            return _run(lambda: client.create_upload_url(filename, **kwargs))
        raise _bad("op must be 'download' or 'create_upload_url'")

    # ================================================================
    # Hooks (webhooks) — full CRUD
    # ================================================================

    @mcp.tool()
    def grain_hook(
        op: Literal["list", "create", "delete"] = "list",
        hook_id: Optional[str] = None,
        hook_url: Optional[str] = None,
        hook_type: Optional[Literal[
            "recording_added", "recording_updated", "recording_deleted",
            "highlight_added", "highlight_updated", "highlight_deleted",
            "story_added", "story_updated", "story_deleted", "upload_status",
        ]] = None,
        include: Optional[Dict[str, Any]] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> object:
        """A webhook subscription — list, create, or delete one. This is the
        ENTIRE hook surface: Grain has no update/PATCH endpoint for hooks,
        confirmed against the doc page — "list"/"create"/"delete" is
        everything there is, not a partial cover. Unlike most write-capable
        tools here, the full CRUD is exposed: a webhook subscription isn't
        destructive of user data, just integration plumbing.

        Args:
            op: "list" (default) | "create" | "delete".
            hook_id: REQUIRED by "delete". Refused on "list"/"create".
            hook_url: REQUIRED by "create" — an HTTPS endpoint. Grain tests
                reachability at creation time: it must answer 2xx immediately
                or the call fails (confirmed live 2026-08-20 against a real URL).
            hook_type: REQUIRED by "create". "story_*" types are the ONLY way
                to observe Stories — there is no REST endpoint to list/get
                them directly (confirmed); the webhook payload IS the data.
            include: op="create" only — for recording_added/recording_updated,
                same shape as `grain_recording`'s `include`; for
                highlight_added/highlight_updated, `{"transcript": bool,
                "speakers": bool}`; other hook_types take {} (omit).
            filter: op="list" only — {"hook_type"?, "state"?: "enabled"|"disabled"}.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "use op='create' to register a new one, "
                             "op='delete' to target an existing one",
                             hook_id=hook_id, hook_url=hook_url, hook_type=hook_type,
                             include=include)
            body = {"filter": filter} if filter is not None else {}
            return _run(lambda: client.list_hooks(**body))
        if op == "create":
            _refuse_ignored(op, "a new hook has no id yet, `filter` only applies to 'list'",
                             hook_id=hook_id, filter=filter)
            if not hook_url or not hook_type:
                raise _bad("op='create' requires `hook_url` and `hook_type`")
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.create_hook(hook_url, hook_type, **kwargs))
        if op == "delete":
            _refuse_ignored(op, "a deletion only takes `hook_id`",
                             hook_url=hook_url, hook_type=hook_type, include=include, filter=filter)
            if not hook_id:
                raise _bad("op='delete' requires `hook_id`")
            return _run(lambda: client.delete_hook(hook_id))
        raise _bad("op must be 'list', 'create' or 'delete'")

    # ================================================================
    # Org — users, teams, meeting types (all list-only, no filters)
    # ================================================================

    @mcp.tool()
    def grain_org(op: Literal["users", "teams", "meeting_types"] = "users") -> object:
        """Workspace org data — users, teams, or meeting types. No filters:
        each call returns everything the key can see.

        Args:
            op: "users" (default, {id, name, email}) | "teams" ({id, name}) |
                "meeting_types" ({id, name, scope}) — `team`/`meeting_type`
                ids from these feed `grain_recording`'s `filter`.
        """
        client = _client()
        if op == "users":
            return _run(lambda: client.list_users())
        if op == "teams":
            return _run(lambda: client.list_teams())
        if op == "meeting_types":
            return _run(lambda: client.list_meeting_types())
        raise _bad("op must be 'users', 'teams' or 'meeting_types'")
