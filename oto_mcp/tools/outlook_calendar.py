"""Outlook Calendar — a person's Microsoft 365 calendars, via Microsoft Graph
(`CalendarClient`).

Credential = the PERSON's Microsoft 365 account (carrier `microsoft`, OAuth, delegated
permissions) that has authorized THIS service (scope `CALENDAR`); several linked
accounts are chosen by the generic `_account=` axis.

**Surface** — the Google Calendar shape (`tools/calendar.py`):
- `outlook_calendar_calendars` — discovery: the calendars and their ids;
- `outlook_calendar_event` (list/get/create/update/rm) — an event.

⚠️ **Graph mails the attendees by itself** (`oto.tools.microsoft.calendar`): creating an
event with attendees invites them, changing it sends them an update, deleting it as
organizer sends a cancellation — no draft, no way to hold the message back. Where
Google's tool defaults to `send_updates="none"`, Graph has no such switch: a write that
would reach attendees is REFUSED until the call passes `notify_attendees=True`. A write
on an event without attendees needs nothing. The default `op` is a READ (`list`).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from ._microsoft_graph import bad, markdown_html, need, raw, refuse_ignored, run, token

#: The Microsoft service these tools call: its scopes, its card (`auth/microsoft`).
_SERVICE = "outlook_calendar"
_LABEL = "Outlook Calendar"

_OPS_ERROR = "op must be 'list', 'get', 'create', 'update' or 'rm'"


def _invites(event: dict) -> list:
    return event.get("attendees") or []


def _calendrier(k: dict) -> dict:
    return {"id": k.get("id"), "name": k.get("name"),
            "isDefaultCalendar": k.get("isDefaultCalendar"), "canEdit": k.get("canEdit"),
            "owner": ((k.get("owner") or {}).get("address"))}


def _evenement(e: dict) -> dict:
    """An event's view: when, what, who — enough to answer and to change it."""
    return {
        "id": e.get("id"),
        "subject": e.get("subject"),
        "start": e.get("start"),
        "end": e.get("end"),
        "isAllDay": e.get("isAllDay"),
        "location": (e.get("location") or {}).get("displayName"),
        "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get("address"),
        "isOrganizer": e.get("isOrganizer"),
        "attendees": [{"address": (a.get("emailAddress") or {}).get("address"),
                       "response": (a.get("status") or {}).get("response")}
                      for a in _invites(e)],
        "isOnlineMeeting": e.get("isOnlineMeeting"),
        "joinUrl": (e.get("onlineMeeting") or {}).get("joinUrl"),
        "isCancelled": e.get("isCancelled"),
        "showAs": e.get("showAs"),
        "preview": e.get("bodyPreview"),
        "webLink": e.get("webLink"),
    }


def _refus_participants(geste: str, adresses: list) -> None:
    raise bad(
        f"This {geste} would email {len(adresses)} attendee(s) ({', '.join(adresses)}): "
        "Microsoft Graph writes to the attendees ITSELF — an invitation on create, an "
        "update on change, a cancellation on delete — with no draft and no way to hold "
        "the message back. Pass `notify_attendees=True` once the person has agreed that "
        "they be written to. Nothing was done.")


def register(mcp: FastMCP) -> None:
    from oto.tools.microsoft import CalendarClient

    def _client() -> CalendarClient:
        """The Graph client of THIS caller, with their current token."""
        return CalendarClient(token(_SERVICE))

    def _run(fn):
        return run(fn, _LABEL)

    @mcp.tool()
    def outlook_calendar_calendars(full: bool = False) -> dict:
        """List the person's Outlook calendars.

        Returns {calendars: [{id, name, isDefaultCalendar, canEdit, owner}], count}.
        Pass an `id` as `calendar_id` to outlook_calendar_event; omit it for the default
        calendar.

        Args:
            full: return Graph's raw calendar objects instead of the trimmed view.
        """
        c = _client()
        calendriers = _run(c.list_calendars)
        return {"calendars": [raw(k) if full else _calendrier(k) for k in calendriers],
                "count": len(calendriers)}

    @mcp.tool()
    def outlook_calendar_event(
        op: Literal["list", "get", "create", "update", "rm"] = "list",
        calendar_id: Optional[str] = None,
        event_id: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        timezone: str = "UTC",
        subject: Optional[str] = None,
        body: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        online_meeting: Optional[bool] = None,
        notify_attendees: bool = False,
        limit: int = 50,
        full: bool = False,
    ) -> dict:
        """An event in an Outlook calendar — list a period, read, create, change or
        delete one.

        `op`:
        - **"list"** (default): the events between `start` and `end` (both required,
          ISO 8601), recurrences expanded into their occurrences, earliest first, in
          the default calendar unless `calendar_id`. Times come back in `timezone`.
        - **"get"**: one event by `event_id` (adds `preview` of its body).
        - **"create"**: ⚠️ WRITES — a new event: `subject`, `start`, `end` (local
          date-times like `2026-10-08T14:00:00`, read in `timezone`), optional `body`
          (markdown), `location`, `attendees`, `online_meeting=True` for a Teams link.
        - **"update"**: ⚠️ WRITES — changes only what you pass (`subject`, `start`/`end`
          — both, read in `timezone` —, `body`, `location`, `online_meeting`).
          `attendees`, when passed, REPLACES the whole list: read it with op="get".
        - **"rm"**: ⚠️ deletes `event_id`. Returns what was deleted, read before.

        ⚠️ **Attendees are emailed by Microsoft itself** — invited on create, updated on
        any change, cancelled on delete; there is no draft step. Such a write is
        REFUSED unless `notify_attendees=True`: ask the person first. An event without
        attendees needs nothing.

        Args:
            op: list (default) | get | create | update | rm.
            calendar_id: the calendar (from outlook_calendar_calendars); default one
                when omitted. op="list"/"create".
            event_id: op="get"/"update"/"rm" — the event id (from op="list").
            start: op="list" — window start (`2026-10-08T00:00:00Z`); op="create"/
                "update" — the event start, local to `timezone`.
            end: same as `start`, for the end.
            timezone: zone of the times given and returned — IANA (`Europe/Paris`) or
                Windows (`Romance Standard Time`); default UTC.
            subject: op="create"/"update" — the title.
            body: op="create"/"update" — the description, in markdown.
            location: op="create"/"update" — the place's name.
            attendees: op="create"/"update" — attendee email addresses; on update it
                REPLACES the list (`[]` removes everyone).
            online_meeting: op="create"/"update" — True adds a Teams meeting link.
            notify_attendees: True to accept that Microsoft emails the attendees
                (required for any write on an event that has, or gets, attendees).
            limit: op="list" — max events (default 50).
            full: return Graph's raw events instead of the trimmed view.
        """
        vue = raw if full else _evenement

        if op == "list":
            refuse_ignored(op, event_id=event_id, subject=subject, body=body,
                           location=location, attendees=attendees,
                           online_meeting=online_meeting)
            debut, fin = need(start, "start", op), need(end, "end", op)
            c = _client()
            evenements = _run(lambda: c.list_events(start=debut, end=fin,
                                                    calendar_id=calendar_id, limit=limit,
                                                    timezone=timezone))
            return {"events": [vue(e) for e in evenements], "count": len(evenements)}

        if op == "get":
            refuse_ignored(op, calendar_id=calendar_id, start=start, end=end,
                           subject=subject, body=body, location=location,
                           attendees=attendees, online_meeting=online_meeting)
            eid = need(event_id, "event_id", op)
            c = _client()
            return vue(_run(lambda: c.get_event(eid, timezone=timezone)))

        if op == "create":
            refuse_ignored(op, event_id=event_id)
            titre = need(subject, "subject", op)
            debut, fin = need(start, "start", op), need(end, "end", op)
            if attendees and not notify_attendees:
                _refus_participants("event creation", list(attendees))
            c = _client()
            return vue(_run(lambda: c.create_event(
                subject=titre, start=debut, end=fin, timezone=timezone,
                attendees=attendees or (),
                body_html=markdown_html(body) if body is not None else None,
                location=location, online_meeting=bool(online_meeting),
                calendar_id=calendar_id)))

        if op == "update":
            refuse_ignored(op, calendar_id=calendar_id)
            eid = need(event_id, "event_id", op)
            if (start is None) != (end is None):
                raise bad("op='update' moves an event with `start` AND `end` (both).")
            patch: dict = {}
            if subject is not None:
                patch["subject"] = subject
            if start is not None:
                patch["start"] = {"dateTime": start, "timeZone": timezone}
                patch["end"] = {"dateTime": end, "timeZone": timezone}
            if body is not None:
                patch["body"] = {"contentType": "HTML", "content": markdown_html(body)}
            if location is not None:
                patch["location"] = {"displayName": location}
            if attendees is not None:
                patch["attendees"] = [{"emailAddress": {"address": a}, "type": "required"}
                                      for a in attendees]
            if online_meeting is not None:
                patch["isOnlineMeeting"] = online_meeting
                if online_meeting:
                    patch["onlineMeetingProvider"] = "teamsForBusiness"
            if not patch:
                raise bad("op='update' needs at least one field to change (subject, "
                          "start/end, body, location, attendees, online_meeting).")
            c = _client()
            if not notify_attendees:
                avant = _run(lambda: c.get_event(eid, timezone=timezone))
                touches = sorted({(a.get("emailAddress") or {}).get("address") or "?"
                                  for a in _invites(avant)} | set(attendees or ()))
                if touches:
                    _refus_participants("event change", touches)
            return vue(_run(lambda: c.update_event(eid, patch)))

        if op == "rm":
            refuse_ignored(op, calendar_id=calendar_id, start=start, end=end,
                           subject=subject, body=body, location=location,
                           attendees=attendees, online_meeting=online_meeting)
            eid = need(event_id, "event_id", op)
            c = _client()
            avant = _run(lambda: c.get_event(eid, timezone=timezone))
            invites = [(a.get("emailAddress") or {}).get("address") or "?"
                       for a in _invites(avant)]
            # Only the ORGANIZER's deletion is a cancellation sent to everyone.
            if invites and avant.get("isOrganizer") and not notify_attendees:
                _refus_participants("deletion", invites)
            _run(lambda: c.delete_event(eid))
            return {"deleted": eid, "subject": avant.get("subject"),
                    "start": avant.get("start")}

        raise bad(_OPS_ERROR)
