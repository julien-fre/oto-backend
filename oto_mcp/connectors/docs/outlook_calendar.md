## prerequisite — connect with your Microsoft 365 account

on the "Outlook Calendar" card, click **Authorize Outlook Calendar** and choose your work account. Nothing else to install or register. The account itself is the "Microsoft 365 account" connector: the calendar borrows it and only adds its own permission (read and write your calendars).
- the agent acts **with your rights**, in **your** calendars; each person in the org connects their own account
- work or school accounts only; a guest of a client's Microsoft 365 fills in **Client directory** at connection (the client's domain; see the "Microsoft 365 account" connector)
- ⚠️ **many organizations require a Microsoft 365 administrator to authorize oto a first time.** If Microsoft shows "admin approval required", an administrator approves oto once for the whole organization (`microsoft_admin_consent`, see the "Microsoft 365 account" connector), then everyone can connect
- an account already linked for another Microsoft service only needs to authorize this one; an account that has not authorized the calendar is refused by the tools, naming this card

## usage — read and plan

`outlook_calendar_calendars` (the calendars and their ids) and `outlook_calendar_event(op=list|get|create|update|rm)`, under the account the call names (`_account=`), otherwise the default one.
- "my meetings this week" → `outlook_calendar_event(start="2026-10-12T00:00:00Z", end="2026-10-17T00:00:00Z", timezone="Europe/Paris")`: recurring meetings come back as their occurrences
- "block Thursday 2 pm for the report" → `outlook_calendar_event(op="create", subject="Report", start="2026-10-15T14:00:00", end="2026-10-15T15:00:00", timezone="Europe/Paris")`
- "invite Marc, with a Teams link" → `op="create"` with `attendees=["marc@…"]`, `online_meeting=true` **and** `notify_attendees=true`, once you agreed that Marc receives the invitation
- "move it to 4 pm" → `op="update"` with `event_id`, `start` and `end`; "cancel it" → `op="rm"`

## note — what is misleading

- ⚠️ **Microsoft emails the attendees itself**: an invitation on create, an update on any change, a cancellation on delete — there is no draft. So a write on an event that has (or gets) attendees is **refused** unless the call passes `notify_attendees=true`. An event without attendees is written without it
- `attendees` on an update **replaces** the whole list: read it first with `op="get"`
- times given without an offset are read in `timezone` (UTC by default): pass the person's zone (`Europe/Paris`)
- a window (`op="list"`) needs both `start` and `end`

## note — scope

events of your calendars: list a period, read, create, change, delete, Teams meeting link. No room booking, no availability lookup of colleagues, no shared calendar of another person. Mail is the "Outlook" connector.
