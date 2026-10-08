"""Google Calendar — oto-core surface (CalendarClient) exposed per user, multi-account.

Same substrate as Gmail/Tasks: each user connects one or more Google accounts
on `https://manage.oto.cx/` (unified OAuth flow, `calendar` scope included).
The `calendar_*` tools act on the default account, or on the account targeted
by the `account` parameter (the email address). No platform key: strictly
per-user access via OAuth.

The requested scope is `https://www.googleapis.com/auth/calendar` (read AND
write of events; SENSITIVE scope at Google — brand verification at
publication, no CASA audit — see `google_oauth.SCOPES`).

**Consolidated surface (ADR 0047 §Amendment, applied to the calendar product)**: one tool
per business OBJECT, the verb in the `op` parameter — `calendar_event` (list/get/create, all
scoped by `calendar_id`, all returning an event or a list of events).
`calendar_calendars` stays ALONE: it is DISCOVERY with no business parameter
(just `account`), and it produces the `calendar_id` that `calendar_event` consumes —
merging would mix a tool with no target with an always-targeted tool (same case as
`zoho_modules`). The "today / next few days" shortcuts are done by passing
`time_min`/`time_max`.

⚠️ **`op="create"` WRITES into a real calendar.** Two consequences held here: the default
`op` is a READ (`list`) — a call without `op` never creates anything; and a missing
required argument raises an actionable error, never a fallback that would invent a
title or a date.
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..auth import google as google_oauth


def _client_for_user(account: Optional[str] = None):
    """Instantiate an oto-core CalendarClient with the user's credentials.

    `account` (email) targets a specific account; None = default account.
    Raises an actionable McpError if no Google account is connected.
    """
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service="calendar")
    except RuntimeError as e:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
    from oto.tools.google.calendar.lib.calendar_client import CalendarClient
    return CalendarClient(credentials=creds)


_GOOGLE_CLIENT_TIMEOUT_S = 20
# oto-backend#867 lot 2 — see gmail.py::_client_for_user_async for the
# justification (same token-refresh mechanism, same method).
async def _client_for_user_async(account: Optional[str] = None):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_GOOGLE_CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=f"Google did not respond within {_GOOGLE_CLIENT_TIMEOUT_S}s "
                    "(token refresh) — retry."))


def register(mcp: FastMCP) -> None:

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Required argument for THIS op — actionable error, never a fallback.

        ⚠️ `op="create"` writes into a real calendar: filling a gap with a default
        would create an event nobody asked for.
        """
        if value is None:
            raise _bad(f"op='{op}' requires {name}")
        return value

    @mcp.tool()
    async def calendar_calendars(account: Optional[str] = None) -> dict:
        """List the Google calendars the user can access.

        Returns {calendars: [{id, summary, primary, accessRole}]}. Use an `id`
        as the `calendar_id` argument of calendar_event; omit it for the user's
        main calendar ('primary').

        Args:
            account: email of the Google account to use — same choice as `_account`.
        """
        client = await _client_for_user_async(account)
        calendars = await asyncio.to_thread(client.list_calendars)
        return {"calendars": calendars, "count": len(calendars)}

    @mcp.tool()
    async def calendar_event(
        op: Literal["list", "get", "create", "update", "rm"] = "list",
        calendar_id: str = "primary",
        event_id: Optional[str] = None,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        query: Optional[str] = None,
        max_results: int = 20,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        all_day: bool = False,
        send_updates: Literal["none", "all", "externalOnly"] = "none",
        account: Optional[str] = None,
    ) -> dict:
        """An event in a calendar — list, read, create, fix or delete one.

        `op`:
        - **"list"** (default): list events from a calendar over a time range
          (ordered by start). Returns {events: [{id, summary, start, end, ...}],
          count}.
        - **"get"**: get a single calendar event by id (`event_id`, detailed —
          adds description, attendees, recurrence, reminders).
        - **"create"**: create a calendar event (`summary` + `start`, optional
          `attendees` = guest emails). ⚠️ **Writes into a real calendar.** With
          `send_updates="none"` (default) the guests are added but nobody is
          emailed — pass "all" to send the invitations.
        - **"update"**: fix an existing event (`event_id` + what changes). PATCHES —
          only the fields you pass are touched, everything else (attendees,
          recurrence, reminders, meeting link) is left alone. Use this instead of
          creating a second event. `attendees`, when passed, REPLACES the whole
          guest list (Google's rule for lists): pass everyone — read the current
          guests with op="get" — and `[]` removes them all.
        - **"rm"**: delete an event (`event_id`). Irreversible. Returns what was
          deleted (summary + start), read before the deletion — so that deleting the
          wrong id does not look exactly like deleting the right one.

        Args:
            op: list (default) | get | create | update | rm.
            calendar_id: calendar id (default 'primary'). Ids come from
                calendar_calendars.
            event_id: op="get" — the event id (from op="list").
            time_min: op="list" — lower bound, ISO 8601 (e.g.
                '2026-06-18T00:00:00Z'). For "today" pass today's 00:00; for
                "next 7 days" pass now.
            time_max: op="list" — upper bound, ISO 8601. Omit either bound to
                leave it open.
            query: op="list" — free-text search over event fields.
            max_results: op="list" — max events to return (default 20).
            summary: op="create" — event title.
            start: op="create" — start time: ISO 8601 datetime (timed) or
                'YYYY-MM-DD' (all-day).
            end: op="create" — end time. If omitted, defaults to start + 1h
                (timed) or same day (all-day). ⚠️ For an all-day event Google
                reads the end DATE as exclusive: pass the next day to cover one
                full day.
            description: op="create" — event description.
            location: op="create" — event location.
            attendees: op="create"/"update" — guest email addresses. On update
                it REPLACES the guest list (`[]` removes every guest).
            all_day: op="create" — treat start/end as dates (YYYY-MM-DD).
                ⚠️ A 10-character `start` ('YYYY-MM-DD') is treated as all-day
                even when all_day is False (CalendarClient.create_event).
            send_updates: op="create"/"update"/"rm" — whether attendees get an email.
                Default **"none"**: fixing a typo must not mail twelve people, and
                cancelling silently is the lesser surprise. Pass "all" deliberately.
            account: email of the Google account to use — same choice as `_account`.
        """
        client = await _client_for_user_async(account)

        if op == "list":
            # ⚠️ the client's POSITIONAL order: (calendar_id, time_min, time_max,
            # max_results, query) — `max_results` BEFORE `query`, counter-intuitive.
            events = await asyncio.to_thread(
                client.list_events, calendar_id, time_min, time_max, max_results,
                query,
            )
            return {"events": events, "count": len(events)}
        if op == "get":
            return await asyncio.to_thread(
                client.get_event, _need(event_id, "event_id", op), calendar_id,
            )
        if op == "create":
            return await asyncio.to_thread(
                client.create_event, _need(summary, "summary", op),
                _need(start, "start", op), end, description, location, all_day,
                calendar_id, attendees=attendees, send_updates=send_updates,
            )
        if op == "update":
            # The client REFUSES an empty patch: with no field, the call would spend a
            # write and return a success while changing nothing (signal #686).
            return await asyncio.to_thread(
                client.update_event, _need(event_id, "event_id", op), summary,
                start, end, description, location, all_day, calendar_id,
                send_updates, attendees=attendees,
            )
        if op == "rm":
            return await asyncio.to_thread(
                client.delete_event, _need(event_id, "event_id", op), calendar_id,
                send_updates,
            )
        raise _bad("op must be 'list', 'get', 'create', 'update' or 'rm'")
