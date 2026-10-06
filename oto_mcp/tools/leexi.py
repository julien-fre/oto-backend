"""Leexi tools — recorded calls and meetings, transcripts, notes.

Wraps `oto.tools.leexi.client.LeexiClient` (API v1, Basic `KEY_ID:KEY_SECRET`).
Five tools, one per family of the upstream API: calls, notes, meetings,
users, teams.

Two scopes govern what the organization sees, and they must be told apart
to read an empty result without picking the wrong diagnosis:

- the **call access scope** is attached to the key on the Leexi side (the whole
  company / a user's access / access rules). Out of scope,
  a call is not listed, and when requested directly it answers 404. An empty list
  can therefore be a perfectly valid setting;
- the **permission scopes** (`read_calls`, `write_users`…) decide which
  endpoints are reachable. Without the scope, it is a 403, and the message says so.

⚠️ **A new key only carries `read_calls`**: user and
team writes — which commit the customer's BILLED LICENSES — require scopes
that a Leexi admin must grant explicitly. This connector does not get around that
notch, it NAMES it when upstream refuses. That is also why the connection probe
queries `/calls` and not `/users`: probing elsewhere would make a healthy but restricted
key pass for a dead one.

Client calls are written out in plain form (`_client().list_calls(…)`): that is what
makes them verifiable by the version-skew probe
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """Translates a Leexi refusal into an actionable message.

    This API's codes are unusually expressive (402 = subscription,
    409 = duplicate, 422 = incompatible state): returning them as-is would deprive
    the agent of the only information that distinguishes « retry » from « change
    something ».
    """
    status = e.status_code
    if status == 401:
        return ("Leexi rejected the key (401) — check the API Key ID and Key "
                "Secret configured on this connector (Leexi: Settings → "
                "Company Settings → API Keys).")
    if status == 402:
        return ("Leexi: inactive subscription (402) — the key is good, but the "
                "Leexi account is not in good standing. Nothing to fix on the oto side.")
    if status == 403:
        return ("Leexi denied access (403) — the key exists but it lacks "
                "the scope for this operation. A new key only carries "
                "`read_calls`: the other scopes, and above all "
                "`write_users`/`write_teams` (which commit billed "
                "licenses), are granted by a Leexi admin.")
    if status == 404:
        return ("Leexi: not found (404). ⚠️ On a call, it can also "
                "mean « outside this key's scope » — the call access "
                "scope is set on the Leexi side, not here.")
    if status == 405:
        return ("Leexi: action impossible for this event (405) — past "
                "meeting, or no usable URL.")
    if status == 409:
        return ("Leexi: conflict (409) — already exists. A user email "
                "or team name already taken, a meeting already declared, or "
                "an assistant already launched.")
    if status == 422:
        return (f"Leexi: the request is valid but the resource cannot "
                f"change that way (422) — for example deleting a team that "
                f"still carries users or calls: {e.body}")
    if status == 429:
        return ("Leexi: too many requests (429) — 50/minute, and only "
                "10/minute for call creation. Retry in a moment.")
    if status in (500, 502, 503, 504):
        return f"Leexi is temporarily unavailable (HTTP {status}) — retry later."
    return f"Leexi refused the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Test the connection » probe: a real read on `/calls`.

    ⚠️ Probing `/users` would be the natural reflex and it would be WRONG: a new
    key only carries `read_calls`, so a perfectly valid key would answer
    403 there, and the button would show red on a healthy configuration.
    `/calls` is the only endpoint a default key can honor.

    An empty list is NOT a failure: it is a key whose access scope does not
    cover any call, which is a valid setting on the Leexi side.
    """
    from oto.tools.leexi.client import LeexiClient
    client = LeexiClient(key_id=fields["key_id"], key_secret=fields["key_secret"])
    client.probe()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.leexi.client import LeexiClient

    connector_verify.register("leexi", _verify)

    def _client() -> LeexiClient:
        creds = access.resolve_credential_fields("leexi")
        return LeexiClient(key_id=creds["key_id"],
                           key_secret=creds["key_secret"])

    def _run(fn):
        """Translates a Leexi refusal into an actionable tool error."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, nom: str, op: str):
        if not value:
            raise _bad(f"op='{op}': `{nom}` required.")
        return value

    # --- calls ---------------------------------------------------------------

    @mcp.tool()
    def leexi_calls(
        op: Literal["search", "get", "create", "presign"] = "search",
        call_uuid: Optional[str] = None,
        owner_uuid: Optional[list[str]] = None,
        participating_user_uuid: Optional[list[str]] = None,
        customer_email_address: Optional[list[str]] = None,
        customer_phone_number: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_filter: Optional[str] = None,
        order: Optional[str] = None,
        with_transcript: bool = False,
        fields: Optional[dict] = None,
        extension: Optional[str] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — recorded calls and meetings, and their transcripts.

        This is where you find what was SAID: `op='get'` returns the call with
        its topics and transcript (paragraph- and word-level), whereas
        `op='search'` only returns the metadata.

        `op`:
        - `search` — lists the calls within the key's scope. Filters by
          owner, participant, customer email or phone, and date window.
          ⚠️ An empty list may simply mean the key has no access
          to any call: the scope is set on the Leexi side.
        - `get` — one call WITH its transcript (`call_uuid`).
        - `create` — registers an imported call (`fields`). Requires that the
          file has already been uploaded via `op='presign'`; creation is
          asynchronous and the summary only arrives several minutes later.
        - `presign` — requests the upload URL for a recording
          (`extension`), first step of an import.

        ⚠️ A 404 on `op='get'` does not mean « does not exist »: a call
        outside the key's scope answers 404, on purpose.

        Args:
            op: search | get | create | presign.
            call_uuid: op='get' — the call to read.
            owner_uuid: op='search' — filter by owner(s).
            participating_user_uuid: op='search' — filter by participant(s).
            customer_email_address: op='search' — filter by customer email(s).
            customer_phone_number: op='search' — filter by customer phone(s).
            date_from: op='search' — start of the window (ISO 8601).
            date_to: op='search' — end of the window (ISO 8601).
            date_filter: op='search' — bounded field: created_at | performed_at | updated_at.
            order: op='search' — sort, e.g. 'performed_at desc'.
            with_transcript: op='search' — attaches the paragraph transcript (heavy response).
            fields: op='create' — call body (direction, external_id,
                performed_at, recording_s3_key, user_uuid required).
            extension: op='presign' — file extension, e.g. 'mp3'.
            page: page number.
            items: rows per page (1-100, default 10).
        """
        if op == "search":
            return _run(lambda: _client().list_calls(
                page=page, items=items, order=order, date_filter=date_filter,
                date_from=date_from, date_to=date_to,
                owner_uuid=owner_uuid,
                participating_user_uuid=participating_user_uuid,
                customer_email_address=customer_email_address,
                customer_phone_number=customer_phone_number,
                with_simple_transcript=with_transcript or None))
        if op == "get":
            _need(call_uuid, "call_uuid", op)
            return _run(lambda: _client().get_call(call_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_call(fields))
        if op == "presign":
            _need(extension, "extension", op)
            return _run(lambda: _client().presign_recording_url(extension))
        raise _bad(f"`op` invalid: {op!r} (expected: search | get | create | presign).")

    # --- notes ---------------------------------------------------------------

    @mcp.tool()
    def leexi_notes(
        op: Literal["list", "get", "update", "delete"] = "list",
        call_uuid: Optional[str] = None,
        note_uuid: Optional[str] = None,
        prompt_uuid: Optional[str] = None,
        locale: Optional[str] = None,
        text: Optional[str] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — the notes produced on a call (summaries, meeting minutes).

        These are the outputs of Leexi prompts: this is where the minutes of a
        meeting live, rather than in the raw transcript.

        `op`:
        - `list` — notes of a call (`call_uuid` required: the API exposes no
          global list). ⚠️ Only notes of category `summary` or `text`
          exist for this API — the absence of the others is not a defect.
        - `get` — one note (`note_uuid`).
        - `update` — REPLACES the text of a language (`locale` + `text`); it is
          not a merge, the previous content of that language is lost.
        - `delete` — deletes a note, no trash.

        Args:
            op: list | get | update | delete.
            call_uuid: op='list' — the call whose notes are read (required).
            note_uuid: op='get'/'update'/'delete' — the targeted note.
            prompt_uuid: op='list' — keep only the notes of this prompt.
            locale: op='update' — language of the rewritten note.
            text: op='update' — the new text (replaces).
            page: page number.
            items: rows per page (1-100, default 10).
        """
        if op == "list":
            _need(call_uuid, "call_uuid", op)
            return _run(lambda: _client().list_call_notes(
                call_uuid, page=page, items=items, prompt_uuid=prompt_uuid))
        if op == "get":
            _need(note_uuid, "note_uuid", op)
            return _run(lambda: _client().get_call_note(note_uuid))
        if op == "update":
            _need(note_uuid, "note_uuid", op)
            _need(locale, "locale", op)
            _need(text, "text", op)
            return _run(lambda: _client().update_call_note(note_uuid, locale, text))
        if op == "delete":
            _need(note_uuid, "note_uuid", op)
            return _run(lambda: _client().delete_call_note(note_uuid))
        raise _bad(f"`op` invalid: {op!r} (expected: list | get | update | delete).")

    # --- meetings ------------------------------------------------------------

    @mcp.tool()
    def leexi_meetings(
        op: Literal["list", "get", "create", "delete", "launch_bot"] = "list",
        meeting_uuid: Optional[str] = None,
        origin: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_filter: Optional[str] = None,
        order: Optional[str] = None,
        fields: Optional[dict] = None,
        stop_task: Optional[bool] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — known meetings, and the assistant sent to them.

        A meeting (« meeting event ») is an appointment that Leexi knows about,
        coming from the calendar, a manual entry or the API — distinct from a
        call, which is an already-processed recording. The assistant is launched on the
        former and produces the latter.

        `op`:
        - `list` — known meetings, filterable by origin and date window.
        - `get` — one meeting (`meeting_uuid`).
        - `create` — declares a meeting (`fields`). `to_record=True` requests
          recording.
        - `delete` — removes a meeting.
        - `launch_bot` — ⚠️ **sends the assistant INTO the meeting**, where the
          participants will see it join. `stop_task=True` does the opposite and
          removes an assistant already running: it is the same upstream endpoint
          for both directions.

        Args:
            op: list | get | create | delete | launch_bot.
            meeting_uuid: the targeted meeting (get, delete, launch_bot).
            origin: op='list' — calendar | manual | api.
            date_from: op='list' — start of the window (ISO 8601).
            date_to: op='list' — end of the window (ISO 8601).
            date_filter: op='list' — bounded field: start_time | end_time.
            order: op='list' — sort, e.g. 'start_time desc'.
            fields: op='create' — body (end_time, internal, meeting_url,
                organizer, owned, start_time, to_record, user_uuid required).
            stop_task: op='launch_bot' — True removes the assistant instead of sending it.
            page: page number.
            items: rows per page (1-100, default 10).
        """
        if op == "list":
            return _run(lambda: _client().list_meeting_events(
                page=page, items=items, order=order, origin=origin,
                date_filter=date_filter, date_from=date_from, date_to=date_to))
        if op == "get":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().get_meeting_event(meeting_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_meeting_event(fields))
        if op == "delete":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().delete_meeting_event(meeting_uuid))
        if op == "launch_bot":
            _need(meeting_uuid, "meeting_uuid", op)
            return _run(lambda: _client().launch_meeting_assistant(
                meeting_uuid, stop_task=stop_task))
        raise _bad(f"`op` invalid: {op!r} "
                   "(expected: list | get | create | delete | launch_bot).")

    # --- users ---------------------------------------------------------------

    @mcp.tool()
    def leexi_users(
        op: Literal["list", "get", "create", "update", "deactivate"] = "list",
        user_uuid: Optional[str] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — the workspace users, and their licenses.

        Mostly used to resolve a `user_uuid` (the one required to create a
        call) and to see who consumes a license.

        ⚠️ **The writes here commit the customer's billing**: creating a
        user consumes a license, and so does reactivating one. They require the
        `write_users` scope, which a new key does NOT have — a Leexi admin must
        grant it, and that is the real safeguard. A 403 refusal says exactly that.

        ⚠️ `deactivate` deletes nothing: calls and history stay,
        sessions drop, the license is freed. The upstream HTTP verb says
        « delete », the effect is a deactivation. Reactivate = `update` with
        `{"active": true}`.

        `op`: `list` | `get` | `create` (fields) | `update` (fields) | `deactivate`.

        Args:
            op: list | get | create | update | deactivate.
            user_uuid: the targeted user (get, update, deactivate).
            fields: op='create' — email, name, team_uuid required; roles,
                license, send_welcome_email optional. op='update' — fields to
                change, including active.
            page: page number.
            items: rows per page (1-100, default 10).
        """
        if op == "list":
            return _run(lambda: _client().list_users(page=page, items=items))
        if op == "get":
            _need(user_uuid, "user_uuid", op)
            return _run(lambda: _client().get_user(user_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_user(fields))
        if op == "update":
            _need(user_uuid, "user_uuid", op)
            _need(fields, "fields", op)
            return _run(lambda: _client().update_user(user_uuid, fields))
        if op == "deactivate":
            _need(user_uuid, "user_uuid", op)
            return _run(lambda: _client().deactivate_user(user_uuid))
        raise _bad(f"`op` invalid: {op!r} "
                   "(expected: list | get | create | update | deactivate).")

    # --- teams ---------------------------------------------------------------

    @mcp.tool()
    def leexi_teams(
        op: Literal["list", "get", "create", "update", "delete"] = "list",
        team_uuid: Optional[str] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        items: int = 10,
    ) -> Any:
        """Leexi — the workspace teams.

        A team carries users and their calls; its `uuid` is required
        to create a user.

        ⚠️ Writes under the `write_teams` scope, which a new key does not have.
        ⚠️ `delete` ONLY works on a team with no users or calls (otherwise
        422): for all the others, deactivate it with
        `op='update' fields={"active": false}`, which the vendor recommends.

        `op`: `list` | `get` | `create` (fields) | `update` (fields) | `delete`.

        Args:
            op: list | get | create | update | delete.
            team_uuid: the targeted team (get, update, delete).
            fields: op='create' — name required, active optional.
                op='update' — name and/or active.
            page: page number.
            items: rows per page (1-100, default 10).
        """
        if op == "list":
            return _run(lambda: _client().list_teams(page=page, items=items))
        if op == "get":
            _need(team_uuid, "team_uuid", op)
            return _run(lambda: _client().get_team(team_uuid))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: _client().create_team(fields))
        if op == "update":
            _need(team_uuid, "team_uuid", op)
            _need(fields, "fields", op)
            return _run(lambda: _client().update_team(team_uuid, fields))
        if op == "delete":
            _need(team_uuid, "team_uuid", op)
            return _run(lambda: _client().delete_team(team_uuid))
        raise _bad(f"`op` invalid: {op!r} "
                   "(expected: list | get | create | update | delete).")
