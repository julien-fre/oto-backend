"""Fireflies.ai — transcripts, live meeting control, AskFred Q&A, org data.

Wraps `oto.tools.fireflies.client.FirefliesClient` (GraphQL, a single
endpoint, Bearer). keyed `api_key`, byo-only (no platform key): each
user/org sets THEIR Fireflies key (app.fireflies.ai/integrations/api).

**7 tools, one per business object** (silae, ADR 0047):
- `fireflies_transcript` — the big one: list/get/delete a transcript,
  transcribe a remote (`upload`) or local (`create_upload_url`
  + `confirm_upload`, flow undocumented by Fireflies, discovered live via
  introspection) audio file, rename/re-privatize/re-channel, share/revoke
  access. op=list/get/delete/upload/update_title/update_privacy/
  update_channel/share/unshare/create_upload_url/confirm_upload.
- `fireflies_live_meeting` — drive a meeting IN PROGRESS: list active
  meetings, inject the Fireflies bot into one, list/create live action
  items, create a live soundbite, change the state (pause/resume).
  op=list_active/add_bot/list_action_items/create_action_item/
  create_soundbite/update_state.
- `fireflies_askfred` — Fireflies' Q&A assistant over one or more
  transcripts: list/read/create/continue/delete a thread.
  op=list_threads/get_thread/create_thread/continue_thread/delete_thread.
- `fireflies_user` — user profile (self or a third party), full directory,
  groups, admin role change. op=get/list/groups/set_role.
- `fireflies_channel` — channels (meeting organization). op=get/list.
- `fireflies_bite` — Soundbites (short excerpts of a meeting). op=get/
  list/create.
- `fireflies_org` — miscellaneous reads without a dedicated object: contacts,
  team/individual analytics, AI Apps outputs, automation rule execution
  logs, audit log (these last two = Enterprise only).
  op=contacts/analytics/apps/rule_executions/audit_events.

**Webhooks = dashboard-only, deliberately absent here.** Fireflies V1
(app.fireflies.ai/settings) AND V2 (app.fireflies.ai/integrations/api/webhook)
are configured EXCLUSIVELY through the web interface — there is no GraphQL
query or mutation to create/list/delete a webhook subscription
(confirmed in the docs). Unlike Granola/Grain, this connector therefore has
no `*_webhook`/`*_hook` tool: it is not a coverage gap, it is an exact
reflection of what the API exposes.

**No param is silently swallowed**: an `op` that does not recognize a supplied
argument REFUSES rather than ignoring it (silae `_refuse_ignored`).

⚠️ **No machine-readable spec exists for this API**
(`docs.fireflies.ai/api-reference/openapi.json` is listed in `llms.txt`
but returns 404 "Asset not found" on fetch, confirmed via `curl` AND WebFetch) —
everything here comes from reading doc pages, never tested live (no
Fireflies key provided in this session). See the docstring of
`FirefliesClient` for the details (including the resolution of the field
`sentence`→`sentences`).

**GraphQL ≠ REST on errors**: an upstream error can arrive as HTTP 200
with an `errors[]` array (`FirefliesGraphQLError`) OR as HTTP >= 400
(`UpstreamHTTPError`, e.g. invalid key) — `_upstream_message` handles both.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """A supplied argument that THIS op does not use is an error of intent,
    not a detail — otherwise a misplaced param would be silently ignored."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _upstream_message(e) -> str:
    from oto.tools.fireflies import FirefliesGraphQLError
    from oto.tools.common.errors import UpstreamHTTPError

    if isinstance(e, UpstreamHTTPError):
        status = e.status_code
        if status in (401, 403):
            return (f"Fireflies rejected the API key (HTTP {status}) — check the key set "
                     "on this connector (app.fireflies.ai/integrations/api).")
        if status == 429:
            return ("Fireflies: too many requests (429) — limit per tier (Free 50/day, "
                     "Pro 500/day, Business/Enterprise 60/min; some mutations have "
                     "their own limit, e.g. deleteTranscript 10/min). Retry later.")
        if status in (500, 502, 503, 504):
            return f"Fireflies is temporarily unavailable (HTTP {status}) — retry later."
        return f"Fireflies refused the request (HTTP {status}): {e.body}"

    if isinstance(e, FirefliesGraphQLError):
        code = e.code or ""
        if code in ("object_not_found",):
            return f"Fireflies: resource not found — {e.message}"
        if code in ("not_in_team", "not_authorized"):
            return f"Fireflies: access denied — {e.message}"
        if code == "require_elevated_privilege":
            return f"Fireflies: insufficient privileges (admin/organizer required) — {e.message}"
        if code == "too_many_requests":
            return f"Fireflies: too many requests — {e.message}"
        return f"Fireflies refused the request: {e.message}"

    return str(e)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Test the connection » probe: the profile of the key's holder, the
    lightest of the reads (no filter, a single object)."""
    from oto.tools.fireflies.client import FirefliesClient
    FirefliesClient(api_key=fields["key"]).get_user()


def register(mcp: FastMCP) -> None:
    from oto.tools.fireflies.client import FirefliesClient
    from oto.tools.fireflies import FirefliesGraphQLError
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("fireflies", _verify)

    def _client() -> FirefliesClient:
        key, _ = access.resolve_api_key("fireflies")
        return FirefliesClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except (UpstreamHTTPError, FirefliesGraphQLError) as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Transcript & meeting — CRUD + upload + share
    # ================================================================

    @mcp.tool()
    def fireflies_transcript(
        op: Literal["list", "get", "delete", "upload", "update_title", "update_privacy",
                     "update_channel", "share", "unshare",
                     "create_upload_url", "confirm_upload"] = "list",
        transcript_id: Optional[str] = None,
        transcript_ids: Optional[List[str]] = None,
        keyword: Optional[str] = None,
        scope: Optional[Literal["title", "sentences", "all"]] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
        host_email: Optional[str] = None,
        user_id: Optional[str] = None,
        mine: Optional[bool] = None,
        organizers: Optional[List[str]] = None,
        participants: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
        url: Optional[str] = None,
        title: Optional[str] = None,
        webhook: Optional[str] = None,
        custom_language: Optional[str] = None,
        save_video: Optional[bool] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        privacy: Optional[Literal["link", "owner", "participants",
                                   "participatingteammates", "teammatesandparticipants",
                                   "teammates"]] = None,
        emails: Optional[List[str]] = None,
        expiry_days: Optional[int] = None,
        email: Optional[str] = None,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
        meeting_id: Optional[str] = None,
    ) -> object:
        """A meeting transcript — list/search, fetch one (full sentences +
        summary + analytics), delete, transcribe a remote audio/video file,
        rename, change privacy, assign a channel, share/revoke access, or
        get/confirm a direct-file-upload URL.

        Note: the byte-PUT step between "create_upload_url" and
        "confirm_upload" is NOT a tool here — a raw binary payload has no
        natural fit for a JSON tool call (same reasoning as Grain's
        `grain_recording_file`). `oto.tools.fireflies.client.FirefliesClient
        .upload_file_bytes` is available for scripted use outside the agent
        loop.

        Args:
            op: "list" (default, search) | "get" | "delete" | "upload"
                (transcribe a PUBLIC URL) | "update_title" | "update_privacy" |
                "update_channel" | "share" | "unshare" | "create_upload_url"
                (get a one-time URL for a LOCAL file, undocumented by
                Fireflies but confirmed live — pair with the byte-PUT done
                outside this tool, then "confirm_upload") | "confirm_upload".
            transcript_id: REQUIRED by "get"/"delete"/"update_title"/
                "update_privacy"/"share"/"unshare" (the last two call it the
                meeting id — same thing in Fireflies).
            transcript_ids: REQUIRED by "update_channel" — 1-5 ids,
                all-or-nothing (any invalid id fails the whole call). This
                tool checks `channel_id` against `fireflies_channel(op="list")`
                before calling Fireflies and refuses an unknown one — the API
                itself does NOT validate it (a typo would silently stick as
                the transcript's channel, live-confirmed, and there is no way
                to unset a channel once assigned, only replace it).
            keyword: op="list" only — searches title (or spoken words too,
                depending on `scope`).
            scope: op="list" only — "title" (default when `keyword` is set
                without it, live-confirmed), "sentences" (search spoken
                words), or "all". REQUIRES `keyword` to also be set —
                Fireflies rejects `scope` passed alone.
            from_date/to_date/limit/skip/host_email/user_id/mine/organizers/
                participants/channel_id: op="list" only — further filters.
                `limit` caps at 50. `mine=True` scopes to the key owner's own
                meetings. (`channel_id` here filters op="list"'s results —
                unrelated to `update_channel`'s target channel above.)
            url: REQUIRED by "upload" — HTTPS URL of the media file, must be
                publicly accessible.
            title: "upload" (meeting title) or "update_title" (new title,
                admin-only per the docs — live-tested by the connector's
                author and NOT rejected, so treat "admin-only" as unverified).
            webhook/custom_language/save_video/attendees: op="upload" only —
                `attendees` is a list of {"displayName"?, "email"?,
                "phoneNumber"?}.
            privacy: REQUIRED by "update_privacy" — the 6-value
                `MeetingPrivacy` enum (the docs only ever showed 5; missing
                `participatingteammates`).
            emails: REQUIRED by "share" — up to 50 addresses.
            expiry_days: op="share" only, optional.
            email: REQUIRED by "unshare" — whose access to revoke.
            content_type: REQUIRED by "create_upload_url" — MIME type of the
                local file (e.g. "audio/mpeg").
            file_size: REQUIRED by "create_upload_url" — size in bytes.
            meeting_id: REQUIRED by "confirm_upload" — the `meeting_id` a
                prior "create_upload_url" call returned.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "use op='get' for a specific transcript",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            if scope is not None and keyword is None:
                raise _bad("op='list': `scope` requires `keyword` (Fireflies refuses `scope` alone)")
            filters = {k: v for k, v in dict(
                keyword=keyword, scope=scope, fromDate=from_date, toDate=to_date, limit=limit,
                skip=skip, host_email=host_email, user_id=user_id, mine=mine,
                organizers=organizers, participants=participants, channel_id=channel_id,
            ).items() if v is not None}
            return _run(lambda: client.list_transcripts(**filters))
        _refuse_ignored(op, "these search filters/parameters only apply to op='list'",
                         keyword=keyword, scope=scope, from_date=from_date, to_date=to_date,
                         host_email=host_email, mine=mine, organizers=organizers,
                         participants=participants)
        if op == "get":
            if not transcript_id:
                raise _bad("op='get' requires `transcript_id`")
            _refuse_ignored(op, "op='get' only takes `transcript_id`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.get_transcript(transcript_id))
        if op == "delete":
            if not transcript_id:
                raise _bad("op='delete' requires `transcript_id`")
            _refuse_ignored(op, "op='delete' only takes `transcript_id`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.delete_transcript(transcript_id))
        if op == "upload":
            _refuse_ignored(op, "op='upload' does not target an existing transcript",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             privacy=privacy, emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            if not url:
                raise _bad("op='upload' requires `url`")
            kwargs = {k: v for k, v in dict(
                title=title, webhook=webhook, custom_language=custom_language,
                save_video=save_video, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.upload_audio(url, **kwargs))
        if op == "update_title":
            if not transcript_id or not title:
                raise _bad("op='update_title' requires `transcript_id` and `title`")
            _refuse_ignored(op, "op='update_title' only takes `transcript_id`/`title`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, webhook=webhook,
                             custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.update_meeting_title(transcript_id, title))
        if op == "update_privacy":
            if not transcript_id or not privacy:
                raise _bad("op='update_privacy' requires `transcript_id` and `privacy`")
            _refuse_ignored(op, "op='update_privacy' only takes `transcript_id`/`privacy`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.update_meeting_privacy(transcript_id, privacy))
        if op == "update_channel":
            if not transcript_ids or not channel_id:
                raise _bad("op='update_channel' requires `transcript_ids` and `channel_id`")
            _refuse_ignored(op, "op='update_channel' only takes `transcript_ids`/`channel_id`",
                             transcript_id=transcript_id, limit=limit, skip=skip, user_id=user_id,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            known_channel_ids = {c["id"] for c in _run(lambda: client.list_channels())}
            if channel_id not in known_channel_ids:
                raise _bad(
                    f"op='update_channel': channel_id={channel_id!r} not found or "
                    "inaccessible with this key (absent from fireflies_channel(op='list') — "
                    "may also be a private channel you are not a member of) — Fireflies "
                    "does NOT raise an error if the id is invalid, it stores the value as "
                    "is, on a channel that can no longer be removed afterwards. Check the id "
                    "first.")
            return _run(lambda: client.update_meeting_channel(transcript_ids, channel_id))
        if op == "share":
            if not transcript_id or not emails:
                raise _bad("op='share' requires `transcript_id` (the meeting id) and `emails`")
            _refuse_ignored(op, "op='share' only takes `transcript_id`/`emails`/`expiry_days`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.share_meeting(transcript_id, emails, expiry_days=expiry_days))
        if op == "unshare":
            if not transcript_id or not email:
                raise _bad("op='unshare' requires `transcript_id` (the meeting id) and `email`")
            _refuse_ignored(op, "op='unshare' only takes `transcript_id`/`email`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails, expiry_days=expiry_days,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.revoke_shared_meeting_access(transcript_id, email))
        if op == "create_upload_url":
            _refuse_ignored(op, "op='create_upload_url' does not target an existing transcript",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             url=url, webhook=webhook, save_video=save_video, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email, meeting_id=meeting_id)
            if content_type is None or file_size is None:
                raise _bad("op='create_upload_url' requires `content_type` and `file_size`")
            kwargs = {k: v for k, v in dict(
                title=title, custom_language=custom_language, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.create_upload_url(content_type, file_size, **kwargs))
        if op == "confirm_upload":
            _refuse_ignored(op, "op='confirm_upload' only takes `meeting_id`",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size)
            if not meeting_id:
                raise _bad("op='confirm_upload' requires `meeting_id` (returned by 'create_upload_url')")
            return _run(lambda: client.confirm_upload(meeting_id))
        raise _bad("unknown op")

    # ================================================================
    # Live meeting control
    # ================================================================

    @mcp.tool()
    def fireflies_live_meeting(
        op: Literal["list_active", "add_bot", "list_action_items", "create_action_item",
                     "create_soundbite", "update_state"] = "list_active",
        meeting_id: Optional[str] = None,
        email: Optional[str] = None,
        states: Optional[List[Literal["active", "paused"]]] = None,
        meeting_link: Optional[str] = None,
        title: Optional[str] = None,
        meeting_password: Optional[str] = None,
        duration: Optional[int] = None,
        language: Optional[str] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        prompt: Optional[str] = None,
        action: Optional[Literal["pause_recording", "resume_recording"]] = None,
    ) -> object:
        """Control a meeting the Fireflies bot is CURRENTLY recording — list
        active meetings, drop the bot into one, capture live action items /
        soundbites, or change its recording state.

        Args:
            op: "list_active" (default) | "add_bot" | "list_action_items" |
                "create_action_item" | "create_soundbite" | "update_state".
            meeting_id: REQUIRED by "list_action_items"/"create_action_item"/
                "create_soundbite"/"update_state".
            email: op="list_active" only — non-admins can only pass their own.
            states: op="list_active" only — "active"/"paused", defaults to both.
            meeting_link: REQUIRED by "add_bot" — Zoom/Meet/Teams/etc URL.
            title/meeting_password/duration/language/attendees: op="add_bot"
                only — `duration` in minutes (15-120, default 60).
            prompt: REQUIRED by "create_action_item"/"create_soundbite" —
                natural-language description; both need AI credits, rate
                limited 10/hour.
            action: REQUIRED by "update_state" — "pause_recording" or
                "resume_recording" (the full `MeetingStateAction` enum,
                confirmed live via introspection 2026-08-20 — the docs never
                enumerated it). Rate limited 10/hour. `add_bot` itself is
                rate limited 3/20min.
        """
        client = _client()
        if op == "list_active":
            _refuse_ignored(op, "op='list_active' only takes `email`/`states`",
                             meeting_id=meeting_id, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt, action=action)
            return _run(lambda: client.list_active_meetings(email=email, states=states))
        if op == "add_bot":
            _refuse_ignored(op, "op='add_bot' does not take these parameters",
                             meeting_id=meeting_id, email=email, states=states, prompt=prompt, action=action)
            if not meeting_link:
                raise _bad("op='add_bot' requires `meeting_link`")
            kwargs = {k: v for k, v in dict(
                title=title, meeting_password=meeting_password, duration=duration,
                language=language, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.add_to_live_meeting(meeting_link, **kwargs))
        if not meeting_id:
            raise _bad(f"op={op!r} requires `meeting_id`")
        if op == "list_action_items":
            _refuse_ignored(op, "op='list_action_items' only takes `meeting_id`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt, action=action)
            return _run(lambda: client.list_live_action_items(meeting_id))
        if op in ("create_action_item", "create_soundbite"):
            _refuse_ignored(op, f"op={op!r} only takes `meeting_id`/`prompt`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, action=action)
            if not prompt:
                raise _bad(f"op={op!r} requires `prompt`")
            if op == "create_action_item":
                return _run(lambda: client.create_live_action_item(meeting_id, prompt))
            return _run(lambda: client.create_live_soundbite(meeting_id, prompt))
        if op == "update_state":
            _refuse_ignored(op, "op='update_state' only takes `meeting_id`/`action`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt)
            if not action:
                raise _bad("op='update_state' requires `action`")
            return _run(lambda: client.update_meeting_state(meeting_id, action))
        raise _bad("unknown op")

    # ================================================================
    # AskFred — Q&A threads over one or more transcripts
    # ================================================================

    @mcp.tool()
    def fireflies_askfred(
        op: Literal["list_threads", "get_thread", "create_thread",
                     "continue_thread", "delete_thread"] = "list_threads",
        thread_id: Optional[str] = None,
        transcript_id: Optional[str] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        response_language: Optional[str] = None,
        format_mode: Optional[Literal["markdown", "plaintext"]] = None,
    ) -> object:
        """AskFred — Fireflies' AI Q&A over meeting transcripts. List/read
        threads, ask a new question, follow up in an existing thread, or
        delete one.

        Args:
            op: "list_threads" (default) | "get_thread" | "create_thread" |
                "continue_thread" | "delete_thread".
            thread_id: REQUIRED by "get_thread"/"continue_thread"/"delete_thread".
            transcript_id: op="list_threads" (optional filter) or
                "create_thread" (optional — scopes the question to one
                meeting; if set, `filters` is ignored).
            query: REQUIRED by "create_thread"/"continue_thread" — the
                question, max 2000 chars.
            filters: op="create_thread" only, ignored if `transcript_id` is
                set — {"start_time"?, "end_time"?, "channel_ids"?,
                "organizers"?, "participants"?, "transcript_ids"?} to search
                across multiple meetings.
            response_language/format_mode: "create_thread"/"continue_thread"
                only.
        """
        client = _client()
        if op == "list_threads":
            _refuse_ignored(op, "op='list_threads' only takes `transcript_id`",
                             thread_id=thread_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.list_askfred_threads(transcript_id=transcript_id))
        if op == "get_thread":
            if not thread_id:
                raise _bad("op='get_thread' requires `thread_id`")
            _refuse_ignored(op, "op='get_thread' only takes `thread_id`",
                             transcript_id=transcript_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.get_askfred_thread(thread_id))
        if op == "create_thread":
            _refuse_ignored(op, "op='create_thread' does not take `thread_id`", thread_id=thread_id)
            if not query:
                raise _bad("op='create_thread' requires `query`")
            kwargs = {k: v for k, v in dict(
                transcript_id=transcript_id, filters=filters, response_language=response_language,
                format_mode=format_mode,
            ).items() if v is not None}
            return _run(lambda: client.create_askfred_thread(query, **kwargs))
        if op == "continue_thread":
            _refuse_ignored(op, "op='continue_thread' does not take `transcript_id`/`filters`",
                             transcript_id=transcript_id, filters=filters)
            if not thread_id or not query:
                raise _bad("op='continue_thread' requires `thread_id` and `query`")
            kwargs = {k: v for k, v in dict(
                response_language=response_language, format_mode=format_mode,
            ).items() if v is not None}
            return _run(lambda: client.continue_askfred_thread(thread_id, query, **kwargs))
        if op == "delete_thread":
            if not thread_id:
                raise _bad("op='delete_thread' requires `thread_id`")
            _refuse_ignored(op, "op='delete_thread' only takes `thread_id`",
                             transcript_id=transcript_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.delete_askfred_thread(thread_id))
        raise _bad("unknown op")

    # ================================================================
    # User — profile, directory, groups, role
    # ================================================================

    @mcp.tool()
    def fireflies_user(
        op: Literal["get", "list", "groups", "set_role"] = "get",
        user_id: Optional[str] = None,
        mine: Optional[bool] = None,
        role: Optional[Literal["admin", "user"]] = None,
    ) -> object:
        """A Fireflies user profile, the team directory, user groups, or
        setting a user's admin role.

        Args:
            op: "get" (default, omit `user_id` for the key owner) | "list"
                (whole team) | "groups" | "set_role".
            user_id: op="get" (optional) or "set_role" (required).
            mine: op="groups" only — True scopes to the caller's own groups.
            role: REQUIRED by "set_role" — "admin" or "user".
        """
        client = _client()
        if op == "get":
            _refuse_ignored(op, "op='get' only takes `user_id`", mine=mine, role=role)
            return _run(lambda: client.get_user(user_id))
        if op == "list":
            _refuse_ignored(op, "op='list' takes no parameter", user_id=user_id, mine=mine, role=role)
            return _run(lambda: client.list_users())
        if op == "groups":
            _refuse_ignored(op, "op='groups' only takes `mine`", user_id=user_id, role=role)
            return _run(lambda: client.list_user_groups(mine=mine))
        if op == "set_role":
            if not user_id or not role:
                raise _bad("op='set_role' requires `user_id` and `role`")
            _refuse_ignored(op, "op='set_role' only takes `user_id`/`role`", mine=mine)
            return _run(lambda: client.set_user_role(user_id, role))
        raise _bad("unknown op")

    # ================================================================
    # Channel
    # ================================================================

    @mcp.tool()
    def fireflies_channel(
        op: Literal["get", "list"] = "list",
        channel_id: Optional[str] = None,
    ) -> object:
        """A channel used to organize meetings — fetch one or list every
        channel visible to the caller (public team channels + private ones
        they belong to).

        Args:
            op: "get" (requires `channel_id`) | "list" (default).
            channel_id: REQUIRED by "get". Refused on "list".
        """
        client = _client()
        if op == "get":
            if not channel_id:
                raise _bad("op='get' requires `channel_id`")
            return _run(lambda: client.get_channel(channel_id))
        if op == "list":
            _refuse_ignored(op, "op='list' does not take `channel_id`", channel_id=channel_id)
            return _run(lambda: client.list_channels())
        raise _bad("op must be 'get' or 'list'")

    # ================================================================
    # Bite (Soundbite)
    # ================================================================

    @mcp.tool()
    def fireflies_bite(
        op: Literal["get", "list", "create"] = "list",
        bite_id: Optional[str] = None,
        mine: Optional[bool] = None,
        transcript_id: Optional[str] = None,
        my_team: Optional[bool] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        name: Optional[str] = None,
        media_type: Optional[Literal["video", "audio"]] = None,
        privacies: Optional[List[Literal["public", "team", "participants"]]] = None,
        summary: Optional[str] = None,
    ) -> object:
        """A Soundbite — a short clip cut from a meeting. Fetch, list, or
        create one.

        Args:
            op: "get" | "list" (default) | "create".
            bite_id: REQUIRED by "get".
            mine/my_team: op="list" only — at least one of `mine`,
                `transcript_id`, `my_team` is required by Fireflies.
            transcript_id: op="list" (filter, see above) or "create"
                (required — source transcript).
            limit: op="list" only, caps at 50.
            skip: op="list" only — pagination offset.
            start_time/end_time: REQUIRED by "create" — clip bounds, seconds.
            name/media_type/privacies/summary: op="create" only.
        """
        client = _client()
        if op == "get":
            if not bite_id:
                raise _bad("op='get' requires `bite_id`")
            _refuse_ignored(op, "op='get' only takes `bite_id`",
                             mine=mine, transcript_id=transcript_id, my_team=my_team, limit=limit,
                             skip=skip, start_time=start_time, end_time=end_time, name=name,
                             media_type=media_type, privacies=privacies, summary=summary)
            return _run(lambda: client.get_bite(bite_id))
        if op == "list":
            _refuse_ignored(op, "op='list' only takes mine/transcript_id/my_team/limit/skip",
                             bite_id=bite_id, start_time=start_time, end_time=end_time, name=name,
                             media_type=media_type, privacies=privacies, summary=summary)
            if mine is None and transcript_id is None and my_team is None:
                raise _bad("op='list' requires at least one of `mine`, `transcript_id`, `my_team`")
            return _run(lambda: client.list_bites(
                mine=mine, transcript_id=transcript_id, my_team=my_team, limit=limit, skip=skip))
        if op == "create":
            _refuse_ignored(op, "op='create' does not take mine/my_team/limit/skip",
                             bite_id=bite_id, mine=mine, my_team=my_team, limit=limit, skip=skip)
            if not transcript_id or start_time is None or end_time is None:
                raise _bad("op='create' requires `transcript_id`, `start_time` and `end_time`")
            kwargs = {k: v for k, v in dict(
                name=name, media_type=media_type, privacies=privacies, summary=summary,
            ).items() if v is not None}
            return _run(lambda: client.create_bite(transcript_id, start_time, end_time, **kwargs))
        raise _bad("unknown op")

    # ================================================================
    # Org — contacts, analytics, AI apps, rule executions, audit log
    # ================================================================

    @mcp.tool()
    def fireflies_org(
        op: Literal["contacts", "analytics", "apps", "rule_executions", "audit_events"] = "contacts",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        app_id: Optional[str] = None,
        transcript_id: Optional[str] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        logs_per_meeting: Optional[int] = None,
        rule_filters: Optional[Dict[str, Any]] = None,
        category: Optional[Literal["MEETING_OPERATIONS", "TEAM_OPERATIONS",
                                    "USER_OPERATIONS", "AUTHENTICATION"]] = None,
        action: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_email: Optional[str] = None,
    ) -> object:
        """Read-only org-wide data with no bigger natural home: contacts (met
        via meetings), team/individual analytics, AI App outputs, automation
        rule execution logs, and the compliance audit log.

        Args:
            op: "contacts" (default) | "analytics" | "apps" |
                "rule_executions" (Enterprise plan) | "audit_events"
                (Enterprise + team-admin, **Beta**).
            start_time/end_time: op="analytics" only — ISO 8601.
            app_id/transcript_id/skip/limit: op="apps" only.
            cursor: op="rule_executions" or "audit_events" — pagination
                cursor from a previous `next_cursor`.
            logs_per_meeting/rule_filters: op="rule_executions" only —
                `rule_filters`: {"rule_id"?, "meeting_id"?, "date_from"?,
                "date_to"?, "is_test"?}. `limit` here caps meeting GROUPS
                (1-50, default 10).
            category: REQUIRED by "audit_events".
            action/date_from/date_to/actor_user_id/actor_email: op=
                "audit_events" only (filters). `limit` here caps events
                (1-50, default 20).
        """
        client = _client()
        if op == "contacts":
            _refuse_ignored(op, "op='contacts' takes no parameter",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, limit=limit, cursor=cursor,
                             logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from,
                             date_to=date_to, actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.list_contacts())
        if op == "analytics":
            _refuse_ignored(op, "op='analytics' only takes `start_time`/`end_time`",
                             app_id=app_id, transcript_id=transcript_id, skip=skip, limit=limit,
                             cursor=cursor, logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from, date_to=date_to,
                             actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.get_analytics(start_time=start_time, end_time=end_time))
        if op == "apps":
            _refuse_ignored(op, "op='apps' only takes app_id/transcript_id/skip/limit",
                             start_time=start_time, end_time=end_time, cursor=cursor,
                             logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from, date_to=date_to,
                             actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.list_apps(
                app_id=app_id, transcript_id=transcript_id, skip=skip, limit=limit))
        if op == "rule_executions":
            _refuse_ignored(op, "op='rule_executions' only takes limit/cursor/logs_per_meeting/rule_filters",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, category=category, action=action,
                             date_from=date_from, date_to=date_to, actor_user_id=actor_user_id,
                             actor_email=actor_email)
            return _run(lambda: client.list_rule_executions_by_meeting(
                limit=limit, cursor=cursor, logs_per_meeting=logs_per_meeting, filters=rule_filters))
        if op == "audit_events":
            _refuse_ignored(op, "op='audit_events' does not take these parameters",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, logs_per_meeting=logs_per_meeting,
                             rule_filters=rule_filters)
            if not category:
                raise _bad("op='audit_events' requires `category`")
            return _run(lambda: client.list_audit_events(
                category, limit=limit, cursor=cursor, action=action, date_from=date_from,
                date_to=date_to, actor_user_id=actor_user_id, actor_email=actor_email))
        raise _bad("unknown op")
