"""Allo — a team's calls and texts, contacts CRM, Power Dialer queue, analytics, SMS.

Wraps `oto.tools.allo.AlloClient` (`Authorization: Api-Key <key>`), key resolved
per call via `access.resolve_api_key("allo")` (BYOK, user or org). The key's
scopes bound every call: `allo(op="me")` lists them.

**Surface** (the `<ns>` / `<ns>_act` pair):
- `allo` (read-only) — the key, members and lines; conversations, search across
  calls and texts, one or several items, a recording link; tags; notes; the CRM
  (people, companies, deals, custom fields); the Power Dialer queue; analytics;
- `allo_act` — send a text, rewrite a call summary, mark or archive a
  conversation, tags, notes, CRM people and companies, the Power Dialer queue.

Calls to the client are written out in full (`_client().get_person(…)`): this is
what makes them verifiable by the version-skew probe
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import ACTE, LECTURE

_NAME = "allo"

_READ_OPS = ("me", "users", "numbers", "conversations", "search", "item", "items",
             "recording", "tags", "notes", "people", "person", "companies", "company",
             "deals", "deal", "attributes", "queue", "metrics", "outbound")
_ACT_OPS = ("send_sms", "update_summary", "update_conversation", "create_tag", "add_tags",
            "remove_tag", "create_note", "create_person", "update_person", "create_company",
            "update_company", "create_queue", "update_queue", "add_queue_numbers",
            "remove_queue_numbers")

_PAGED = {"page", "size"}
_QUEUE_OWNER = {"user_id", "email"}
_DATES = {"date_from", "date_to", "compare_from", "compare_to", "user_ids", "allo_numbers"}

#: What each op reads; any other argument given is refused, never dropped.
_READ_TAKES = {
    "me": set(), "numbers": set(), "tags": set(),
    "users": {"role", "status"},
    "conversations": {"allo_number", "last_activity_since", "unread", "transcript"} | _PAGED,
    "search": {"allo_number", "contact_number", "user_id", "direction", "item_type", "result",
               "tags", "unread", "unresponded", "search", "date_from", "date_to", "sort",
               "transcript"} | _PAGED,
    "item": {"item_id", "transcript"},
    "items": {"ids"},
    "recording": {"item_id"},
    "notes": {"person_id", "contact_number", "allo_number"} | _PAGED,
    "people": {"filters", "search", "sort"} | _PAGED,
    "person": {"person_id"},
    "companies": {"filters", "sort"} | _PAGED,
    "company": {"company_id"},
    "deals": {"filters", "search", "sort"} | _PAGED,
    "deal": {"deal_id"},
    "attributes": {"entity"} | _PAGED,
    "queue": _QUEUE_OWNER | _PAGED,
    "metrics": _DATES,
    "outbound": _DATES | {"tags", "granularity", "stage"} | _PAGED,
}
_READ_NEEDS = {
    "conversations": ("allo_number",), "item": ("item_id",), "items": ("ids",),
    "recording": ("item_id",), "person": ("person_id",), "company": ("company_id",),
    "deal": ("deal_id",), "attributes": ("entity",),
    "metrics": ("date_from", "date_to"), "outbound": ("date_from", "date_to"),
}

_ACT_TAKES = {
    "send_sms": {"to", "message", "from_number", "sender_id"},
    "update_summary": {"item_id", "content", "template_key"},
    "update_conversation": {"contact_number", "action", "allo_number"},
    "create_tag": {"name", "color"},
    "add_tags": {"item_id", "tags"},
    "remove_tag": {"item_id", "tag"},
    "create_note": {"content", "person_id", "contact_number", "allo_number", "sender_id"},
    "create_person": {"fields"},
    "update_person": {"person_id", "fields"},
    "create_company": {"fields"},
    "update_company": {"company_id", "fields"},
    "create_queue": {"name"} | _QUEUE_OWNER,
    "update_queue": {"name", "voicemail_handling", "do_not_disturb"} | _QUEUE_OWNER,
    "add_queue_numbers": {"entries"} | _QUEUE_OWNER,
    "remove_queue_numbers": {"number", "position", "unassigned"} | _QUEUE_OWNER,
}
_ACT_NEEDS = {
    "send_sms": ("to", "message"), "update_summary": ("item_id", "content"),
    "update_conversation": ("contact_number", "action"), "create_tag": ("name",),
    "add_tags": ("item_id", "tags"), "remove_tag": ("item_id", "tag"),
    "create_note": ("content",), "create_person": ("fields",),
    "update_person": ("person_id", "fields"), "create_company": ("fields",),
    "update_company": ("company_id", "fields"), "add_queue_numbers": ("entries",),
}

#: The fields a CRM record takes in `fields`; any other key is refused before the call.
_PERSON_FIELDS = {"name", "last_name", "job_title", "website", "address", "numbers", "emails",
                  "company_id", "custom_attributes"}
_COMPANY_FIELDS = {"name", "website", "industry", "custom_attributes"}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _check_args(op: str, given: dict, takes: dict, needs: dict) -> None:
    """Required arguments named, ignored ones refused: nothing is done either way."""
    for name in needs.get(op, ()):
        if given.get(name) in (None, "", [], {}):
            raise _bad(f"`{name}` is required for op='{op}'. Nothing was done.")
    stray = sorted(k for k, v in given.items()
                   if v is not None and v is not False and k not in takes[op])
    if stray:
        raise _bad(f"op='{op}' does not take {', '.join(f'`{k}`' for k in stray)}. "
                   "Nothing was done.")


def _range(start: Optional[str], end: Optional[str], name: str) -> Optional[dict]:
    if start is None and end is None:
        return None
    if not start or not end:
        raise _bad(f"`{name}_from` and `{name}_to` go together (YYYY-MM-DD). Nothing was done.")
    return {"from": start, "to": end}


def _fields(fields: dict, allowed: set, what: str) -> dict:
    stray = sorted(set(fields) - allowed)
    if stray:
        raise _bad(f"A {what} does not take {', '.join(f'`{k}`' for k in stray)} in `fields` "
                   f"(takes {', '.join(sorted(allowed))}). Nothing was done.")
    return fields


def _upstream_message(e) -> str:
    """Turn an Allo refusal into an actionable message: its own code, message and
    suggestion when the body carries them."""
    status = e.status_code
    err = e.body.get("error") if isinstance(e.body, dict) else None
    if isinstance(err, dict):
        detail = " ".join(str(err[k]) for k in ("code", "message", "suggestion") if err.get(k))
    else:
        detail = str(e.body)[:400]
    if status == 401:
        return ("Allo refused the API key (HTTP 401): it is wrong, revoked or expired — "
                "set a new one on the connector.")
    if status == 403:
        return ("Allo: the API key lacks the scope this call needs (HTTP 403). "
                f"`allo(op=\"me\")` lists its scopes; an Allo admin can widen them. {detail}")
    if status == 404:
        return f"Allo: not found (404) — check the id or the phone number (E.164). {detail}"
    if status == 429:
        wait = err.get("retry_after_seconds") if isinstance(err, dict) else None
        return f"Allo: rate limit reached (429) — retry in {wait or 'a few'} seconds."
    if status >= 500:
        return f"Allo is temporarily unavailable (HTTP {status}); retry later."
    return f"Allo refused the request (HTTP {status}): {detail}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Probe for "test the connection": the key itself (`GET /v2/api/me`), which
    needs no scope and changes nothing."""
    from oto.tools.allo import AlloClient
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        AlloClient(api_key=fields.get("key")).probe()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Allo HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Allo HTTP {e.status_code}: {e.body}")
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))


def register(mcp: FastMCP) -> None:
    from oto.tools.allo import AlloClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register(_NAME, _verify)

    def _client() -> AlloClient:
        key, _is_platform = access.resolve_api_key(_NAME)
        return AlloClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except ValueError as e:
            raise _bad(str(e))

    @mcp.tool(name="allo", annotations=LECTURE)
    def allo(
        op: Literal["me", "users", "numbers", "conversations", "search", "item", "items",
                    "recording", "tags", "notes", "people", "person", "companies", "company",
                    "deals", "deal", "attributes", "queue", "metrics", "outbound"],
        allo_number: Optional[str] = None,
        contact_number: Optional[str] = None,
        item_id: Optional[str] = None,
        ids: Optional[list[str]] = None,
        person_id: Optional[str] = None,
        company_id: Optional[str] = None,
        deal_id: Optional[str] = None,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        search: Optional[str] = None,
        filters: Optional[dict] = None,
        direction: Optional[Literal["INBOUND", "OUTBOUND"]] = None,
        item_type: Optional[Literal["CALL", "SMS", "ALL"]] = None,
        result: Optional[Literal["ANSWERED", "VOICEMAIL", "TRANSFERRED"]] = None,
        tags: Optional[list[str]] = None,
        unread: Optional[bool] = None,
        unresponded: Optional[bool] = None,
        last_activity_since: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        compare_from: Optional[str] = None,
        compare_to: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
        allo_numbers: Optional[list[str]] = None,
        granularity: Optional[Literal["DAY", "WEEK", "MONTH"]] = None,
        stage: Optional[Literal["DIAL", "CONNECTED", "CONVERSATION", "CONVERSION"]] = None,
        entity: Optional[Literal["person", "company"]] = None,
        role: Optional[Literal["ADMIN", "MANAGER", "MEMBER"]] = None,
        status: Optional[Literal["ACTIVE", "BLOCKED"]] = None,
        sort: Optional[str] = None,
        transcript: bool = False,
        page: Optional[int] = None,
        size: Optional[int] = None,
    ) -> Any:
        """Allo phone system, read-only: the team's calls and texts with their AI
        summaries, the contacts CRM, the Power Dialer queue and analytics. Sending a
        text and every change are `allo_act`.

        `op`:
        - `me` — the API key: its scopes, the endpoints it may call, the team. Read
          it first when a call is refused for a missing scope.
        - `users` — the members (id, name, email, role, status); `role`, `status`.
        - `numbers` — the team's lines (E.164) and sender ids, with their members.
        - `conversations` — the conversations of one line (`allo_number`), one per
          contact, latest activity first; `unread`, `last_activity_since` (ISO 8601).
        - `search` — calls and texts across every conversation: `search` is a keyword
          over transcripts, summaries and message content; filters `allo_number`,
          `contact_number`, `user_id`, `direction`, `item_type`, `result`, `tags`,
          `unread`, `unresponded`, `date_from`+`date_to`; `sort` DATE_DESC
          (default), DATE_ASC or RELEVANCE. Each item: id (`cll-*` call, `msg-*`
          text), date, duration, result, member, contact, summary, tags.
        - `item` — one call or text (`item_id`); `items` — several (`ids`).
        - `recording` — a signed link to a call's recording (`item_id`), valid 60
          minutes, playable without login: share it with nobody who may not hear it.
        - `tags` — the team's tags (their keys are what `tags` filters take).
        - `notes` — internal notes of a person (`person_id`) or of a conversation
          (`contact_number` + `allo_number`).
        - `people` / `companies` / `deals` — search the CRM: `search` (people,
          deals), `filters` (people: name, last_name, job_title, website, company,
          phone_number, email, deal_status; companies: name, website, industry;
          deals: stage), `sort` (DATE_DESC, DATE_ASC, NAME_ASC, NAME_DESC,
          CREATED_DESC, CREATED_ASC).
        - `person` / `company` / `deal` — one record by id (`per-*`, `com-*`, `dea-*`).
        - `attributes` — the custom fields of `entity` ("person" or "company"), the
          keys `custom_attributes` takes in `allo_act`.
        - `queue` — the Power Dialer queue: its numbers by position and settings;
          the key owner's, or a teammate's by `user_id` or `email`.
        - `metrics` — team KPIs over `date_from`..`date_to` (calls, talk time, calls
          over a minute, answer rate) per member, against `compare_from`..`compare_to`.
        - `outbound` — the outbound funnel (dials → connected → conversations →
          conversions, a conversion being a call tagged with one of `tags`), time
          series by `granularity`, heatmap, leaderboard. With `stage`, the calls
          behind that stage, paged.

        Phone numbers are E.164 (`+33612345678`). Dates are YYYY-MM-DD. Lists page
        with `page` (from 1) and `size` (up to 100), the answer's `pagination`
        saying `has_more`. `transcript=true` adds call transcripts (large).

        Args:
            op: What to read (see above).
            allo_number: One of the team's lines, E.164.
            contact_number: A contact's phone number, E.164.
            item_id: A call (`cll-*`) or text (`msg-*`).
            ids: Several item ids, for `items`.
            person_id: A CRM person (`per-*`).
            company_id: A CRM company (`com-*`).
            deal_id: A CRM deal (`dea-*`).
            user_id: A member: filter of `search`, or whose queue to read.
            email: A member's email, whose queue to read (when no `user_id`).
            search: Keyword search (`search`, `people`, `deals`).
            filters: Field filters of `people`, `companies` or `deals` (see above).
            direction: INBOUND or OUTBOUND (`search`).
            item_type: CALL, SMS or ALL (`search`).
            result: ANSWERED, VOICEMAIL or TRANSFERRED (`search`).
            tags: Tag keys: filter of `search`; what counts as a conversion for `outbound`.
            unread: Only unread (`conversations`, `search`).
            unresponded: Only items not answered yet (`search`).
            last_activity_since: ISO 8601 timestamp (`conversations`).
            date_from: Start date, YYYY-MM-DD (`search`, `metrics`, `outbound`).
            date_to: End date, YYYY-MM-DD.
            compare_from: Start of the comparison period (`metrics`, `outbound`).
            compare_to: End of the comparison period.
            user_ids: Members to count (`metrics`, `outbound`).
            allo_numbers: Lines to count (`metrics`, `outbound`).
            granularity: DAY, WEEK or MONTH (`outbound`).
            stage: Funnel stage whose calls to list (`outbound`).
            entity: "person" or "company" (`attributes`).
            role: Member role filter (`users`).
            status: Member status filter (`users`).
            sort: Sort order (see above).
            transcript: Add the call transcripts (`conversations`, `search`, `item`).
            page: Page number, from 1.
            size: Items per page, up to 100.
        """
        if op not in _READ_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_READ_OPS)}.")
        given = dict(allo_number=allo_number, contact_number=contact_number, item_id=item_id,
                     ids=ids, person_id=person_id, company_id=company_id, deal_id=deal_id,
                     user_id=user_id, email=email, search=search, filters=filters,
                     direction=direction, item_type=item_type, result=result, tags=tags,
                     unread=unread, unresponded=unresponded,
                     last_activity_since=last_activity_since, date_from=date_from,
                     date_to=date_to, compare_from=compare_from, compare_to=compare_to,
                     user_ids=user_ids, allo_numbers=allo_numbers, granularity=granularity,
                     stage=stage, entity=entity, role=role, status=status, sort=sort,
                     transcript=transcript, page=page, size=size)
        _check_args(op, given, _READ_TAKES, _READ_NEEDS)
        extend = "transcript" if transcript else None
        paged = dict(page=page, size=size)

        if op == "me":
            return _run(lambda: _client().get_me())
        if op == "users":
            return _run(lambda: _client().list_users(role=role, status=status))
        if op == "numbers":
            return _run(lambda: _client().list_numbers())
        if op == "conversations":
            return _run(lambda: _client().list_conversations(
                allo_number, last_activity_since=last_activity_since, unread=unread,
                extend=extend, **paged))
        if op == "search":
            date = _range(date_from, date_to, "date")
            return _run(lambda: _client().search_conversation_items(
                allo_number=allo_number, contact_number=contact_number, user_id=user_id,
                direction=direction, type=item_type, result=result, tags=tags, unread=unread,
                unresponded=unresponded, search=search, date=date, sort=sort, extend=extend,
                **paged))
        if op == "item":
            return _run(lambda: _client().get_conversation_item(item_id, extend=extend))
        if op == "items":
            return _run(lambda: _client().get_conversation_items(ids))
        if op == "recording":
            return _run(lambda: _client().get_recording_link(item_id))
        if op == "tags":
            return _run(lambda: _client().list_tags())
        if op == "notes":
            if person_id and (contact_number or allo_number):
                raise _bad("op='notes' reads a person's notes (`person_id`) OR a "
                           "conversation's (`contact_number` + `allo_number`), not both. "
                           "Nothing was done.")
            if person_id:
                return _run(lambda: _client().list_person_notes(person_id, **paged))
            if not (contact_number and allo_number):
                raise _bad("op='notes' needs `person_id`, or `contact_number` and "
                           "`allo_number`. Nothing was done.")
            return _run(lambda: _client().list_conversation_notes(
                contact_number, allo_number, **paged))
        if op == "people":
            return _run(lambda: _client().search_people(
                filters=filters, search=search, sort=sort, **paged))
        if op == "person":
            return _run(lambda: _client().get_person(person_id))
        if op == "companies":
            return _run(lambda: _client().search_companies(filters=filters, sort=sort, **paged))
        if op == "company":
            return _run(lambda: _client().get_company(company_id))
        if op == "deals":
            return _run(lambda: _client().search_deals(
                filters=filters, search=search, sort=sort, **paged))
        if op == "deal":
            return _run(lambda: _client().get_deal(deal_id))
        if op == "attributes":
            if entity == "company":
                return _run(lambda: _client().list_company_attributes(**paged))
            return _run(lambda: _client().list_person_attributes(**paged))
        if op == "queue":
            return _run(lambda: _client().get_dialing_queue(user_id=user_id, email=email,
                                                            **paged))
        date = _range(date_from, date_to, "date")
        compare = _range(compare_from, compare_to, "compare")
        if op == "metrics":
            return _run(lambda: _client().get_team_metrics(
                date, compare_date=compare, user_ids=user_ids, allo_numbers=allo_numbers))
        if (page or size) and not stage:
            raise _bad("op='outbound' pages the calls of a `stage`: give `stage` with "
                       "`page`/`size`. Nothing was done.")
        return _run(lambda: _client().get_outbound_metrics(
            date, compare_date=compare, user_ids=user_ids, allo_numbers=allo_numbers,
            tags=tags, granularity=granularity, extend="items" if stage else None,
            stage=stage, **paged))

    @mcp.tool(name="allo_act", annotations=ACTE)
    def allo_act(
        op: Literal["send_sms", "update_summary", "update_conversation", "create_tag",
                    "add_tags", "remove_tag", "create_note", "create_person", "update_person",
                    "create_company", "update_company", "create_queue", "update_queue",
                    "add_queue_numbers", "remove_queue_numbers"],
        to: Optional[str] = None,
        message: Optional[str] = None,
        from_number: Optional[str] = None,
        sender_id: Optional[str] = None,
        item_id: Optional[str] = None,
        content: Optional[str] = None,
        template_key: Optional[Literal["MARKDOWN_CLASSIC", "MARKDOWN_SHORT_CALL"]] = None,
        contact_number: Optional[str] = None,
        allo_number: Optional[str] = None,
        action: Optional[Literal["READ", "UNREAD", "ARCHIVE", "UNARCHIVE"]] = None,
        name: Optional[str] = None,
        color: Optional[str] = None,
        tags: Optional[list[str]] = None,
        tag: Optional[str] = None,
        person_id: Optional[str] = None,
        company_id: Optional[str] = None,
        fields: Optional[dict] = None,
        entries: Optional[list[dict]] = None,
        number: Optional[str] = None,
        position: Optional[int] = None,
        unassigned: bool = False,
        voicemail_handling: Optional[Literal["SKIP", "NO_SKIP"]] = None,
        do_not_disturb: Optional[Literal["ENABLED", "DISABLED"]] = None,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Any:
        """Act on Allo: send a text, change a call, a conversation, the CRM or the
        Power Dialer queue. Each call acts once (never retried).

        `op`:
        - `send_sms` — send a text NOW to `to` (E.164), billed per segment, from
          one of the team's numbers (`from_number`, recipient in the same country)
          or from a Sender ID verified by Allo (`sender_id`, France): exactly one.
        - `update_summary` — replace a call's AI summary (`item_id`, markdown
          `content`); `template_key` when the call has several summaries.
        - `update_conversation` — `action` READ, UNREAD, ARCHIVE or UNARCHIVE on
          the conversation with `contact_number` (on `allo_number`).
        - `create_tag` — a team tag (`name`, `color`); its key is the slug of the name.
        - `add_tags` / `remove_tag` — tag a call (`item_id`, `tags`: tag keys), or
          untag it (`tag`).
        - `create_note` — an internal note (`content`) on a person (`person_id`) or
          on a conversation (`contact_number`, `allo_number`, optionally written as
          member `sender_id`); the contact never sees it.
        - `create_person` / `update_person` — a CRM person; `fields`: name,
          last_name, job_title, website, address, numbers (E.164, at least one to
          create), emails, company_id, custom_attributes (keys from
          `allo(op="attributes")`). An update changes only the fields given.
        - `create_company` / `update_company` — a CRM company; `fields`: name
          (to create), website, industry, custom_attributes.
        - `create_queue` — a fresh, EMPTY Power Dialer queue (`name`): the current
          queue is replaced.
        - `update_queue` — the queue's `name`, `voicemail_handling`,
          `do_not_disturb`.
        - `add_queue_numbers` — append `entries` to the queue (made if none), each
          `{"number": "+33…", "name", "last_name", "company", "website",
          "job_title", "emails", "addresses"}`; the answer lists added and skipped.
        - `remove_queue_numbers` — by `number` (every occurrence), `position`
          (0-based), both (one occurrence), or `unassigned=true` (every entry not
          called yet).

        The queue ops act on the key owner's queue, or on a teammate's by
        `user_id` or `email`.

        Args:
            op: What to do (see above).
            to: Recipient of the text, E.164.
            message: Text of the SMS.
            from_number: One of the team's numbers, E.164 (`send_sms`).
            sender_id: A Sender ID verified by Allo (`send_sms`, France); or the member a conversation note is written as.
            item_id: A call (`cll-*`).
            content: Summary markdown, or note text.
            template_key: Which summary to replace (`update_summary`).
            contact_number: A contact's phone number, E.164.
            allo_number: The team's line of the conversation, E.164.
            action: READ, UNREAD, ARCHIVE or UNARCHIVE (`update_conversation`).
            name: Tag name, or queue name.
            color: Tag color.
            tags: Tag keys to add to a call.
            tag: Tag key to remove from a call.
            person_id: A CRM person (`per-*`).
            company_id: A CRM company (`com-*`).
            fields: The record's fields (`create_person`, `update_person`, `create_company`, `update_company`).
            entries: Numbers to append to the queue (`add_queue_numbers`).
            number: Number to remove from the queue, E.164.
            position: 0-based queue position to remove.
            unassigned: Remove every queue entry not called yet.
            voicemail_handling: SKIP or NO_SKIP (`update_queue`).
            do_not_disturb: ENABLED or DISABLED while dialing (`update_queue`).
            user_id: A teammate whose queue to act on.
            email: A teammate's email, whose queue to act on (when no `user_id`).
        """
        if op not in _ACT_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_ACT_OPS)}. "
                       "Nothing was done.")
        given = dict(to=to, message=message, from_number=from_number, sender_id=sender_id,
                     item_id=item_id, content=content, template_key=template_key,
                     contact_number=contact_number, allo_number=allo_number, action=action,
                     name=name, color=color, tags=tags, tag=tag, person_id=person_id,
                     company_id=company_id, fields=fields, entries=entries, number=number,
                     position=position, unassigned=unassigned,
                     voicemail_handling=voicemail_handling, do_not_disturb=do_not_disturb,
                     user_id=user_id, email=email)
        _check_args(op, given, _ACT_TAKES, _ACT_NEEDS)
        owner = dict(user_id=user_id, email=email)

        if op == "send_sms":
            if (from_number is None) == (sender_id is None):
                raise _bad("op='send_sms' needs exactly one of `from_number` (a team number) "
                           "or `sender_id` (a verified Sender ID). Nothing was sent.")
            return _run(lambda: _client().send_sms(to, message, from_number=from_number,
                                                   sender_id=sender_id))
        if op == "update_summary":
            return _run(lambda: _client().update_call_summary(item_id, content,
                                                              template_key=template_key))
        if op == "update_conversation":
            return _run(lambda: _client().update_conversation(contact_number, action,
                                                              allo_number=allo_number))
        if op == "create_tag":
            return _run(lambda: _client().create_tag(name, color=color))
        if op == "add_tags":
            return _run(lambda: _client().add_item_tags(item_id, tags))
        if op == "remove_tag":
            return _run(lambda: _client().remove_item_tag(item_id, tag))
        if op == "create_note":
            if person_id and (contact_number or allo_number or sender_id):
                raise _bad("op='create_note' writes on a person (`person_id`) OR on a "
                           "conversation (`contact_number`), not both. Nothing was done.")
            if person_id:
                return _run(lambda: _client().create_person_note(person_id, content))
            if not contact_number:
                raise _bad("op='create_note' needs `person_id` or `contact_number`. "
                           "Nothing was done.")
            return _run(lambda: _client().create_conversation_note(
                contact_number, content, allo_number=allo_number, sender_id=sender_id))
        if op == "create_person":
            body = dict(_fields(fields, _PERSON_FIELDS | {"allow_duplicate_number"}, "person"))
            numbers = body.pop("numbers", None)
            if not numbers:
                raise _bad("op='create_person' needs `fields.numbers` (at least one E.164 "
                           "number). Nothing was done.")
            return _run(lambda: _client().create_person(numbers, **body))
        if op == "update_person":
            body = _fields(fields, _PERSON_FIELDS, "person")
            return _run(lambda: _client().update_person(person_id, **body))
        if op == "create_company":
            body = dict(_fields(fields, _COMPANY_FIELDS, "company"))
            company_name = body.pop("name", None)
            if not company_name:
                raise _bad("op='create_company' needs `fields.name`. Nothing was done.")
            return _run(lambda: _client().create_company(company_name, **body))
        if op == "update_company":
            body = _fields(fields, _COMPANY_FIELDS, "company")
            return _run(lambda: _client().update_company(company_id, **body))
        if op == "create_queue":
            return _run(lambda: _client().reset_dialing_queue(name=name, **owner))
        if op == "update_queue":
            return _run(lambda: _client().update_dialing_queue(
                name=name, voicemail_handling=voicemail_handling,
                do_not_disturb=do_not_disturb, **owner))
        if op == "add_queue_numbers":
            return _run(lambda: _client().add_queue_numbers(entries, **owner))
        return _run(lambda: _client().remove_queue_numbers(
            number=number, position=position, unassigned=unassigned or None, **owner))
