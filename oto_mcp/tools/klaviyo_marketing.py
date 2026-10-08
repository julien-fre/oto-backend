"""Klaviyo — campaigns, flows, metrics, events and performance reports.

Second module of the `klaviyo` connector (`Connector.modules`); the first,
`tools/klaviyo.py`, holds the account, profiles, lists, segments and consent,
and the shared base lives in `klaviyo_socle.py`.

Everything here READS, except `klaviyo_events(op="create")`: recording an event
starts the flows its metric triggers — a preview until `confirm=True`, unless
`backfill=True` (a past event, no flow). Campaigns and flows are read only:
nothing here sends, creates or edits one.

The three report gestures are POSTs that only read. The values reports are paced
by Klaviyo at 2 per minute and 225 per day: the descriptions say so, so that the
agent asks every statistic in one call.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from .klaviyo_socle import (
    PROFILE_ATTRIBUTES, PROFILE_IDENTIFIERS, _bad, _client, hors_op, included_names,
    listing, need, pages, preview, rel_id, run, split_clauses,
)
from .lecture import LECTURE

_LIST_OPS = ("filter", "sort", "page_size", "page_cursor", "all_pages", "max_pages", "full")
_CAMPAIGN_OMITTED = ("send_options", "tracking_options", "send_strategy")
_TIMEFRAMES = Literal["today", "yesterday", "this_week", "last_week", "this_month",
                      "last_month", "this_year", "last_year", "last_7_days",
                      "last_30_days", "last_90_days", "last_3_months",
                      "last_12_months", "last_365_days"]


def _a(item: dict) -> dict:
    return item.get("attributes") or {}


def _slim_campaign(item: dict) -> dict:
    a = _a(item)
    out = {"id": item.get("id"), "name": a.get("name"), "status": a.get("status"),
           "archived": a.get("archived"), "audiences": a.get("audiences"),
           "created_at": a.get("created_at"), "scheduled_at": a.get("scheduled_at"),
           "updated_at": a.get("updated_at"), "send_time": a.get("send_time")}
    return {k: v for k, v in out.items() if v is not None}


def _campaign_record(res: dict) -> dict:
    item = res.get("data") or {}
    out = {"id": item.get("id"), **{k: v for k, v in _a(item).items() if v is not None}}
    included = [i for i in res.get("included") or [] if isinstance(i, dict)]
    messages = [{"id": i.get("id"), **_a(i)} for i in included
                if i.get("type") == "campaign-message"]
    tags = [_a(i).get("name") for i in included if i.get("type") == "tag"]
    if messages:
        out["messages"] = messages
    if tags:
        out["tags"] = tags
    return out


def _slim_flow(item: dict) -> dict:
    a = _a(item)
    out = {"id": item.get("id"), "name": a.get("name"), "status": a.get("status"),
           "archived": a.get("archived"), "trigger_type": a.get("trigger_type"),
           "created": a.get("created"), "updated": a.get("updated"),
           "definition": a.get("definition")}
    return {k: v for k, v in out.items() if v is not None}


def _slim_metric(item: dict) -> dict:
    a = _a(item)
    integration = a.get("integration") or {}
    out = {"id": item.get("id"), "name": a.get("name"), "created": a.get("created"),
           "updated": a.get("updated"), "integration": integration.get("name"),
           "integration_category": integration.get("category")}
    return {k: v for k, v in out.items() if v is not None}


def _event_slimmer(res: dict):
    """An event row, naming its metric and profile when `include` brought them."""
    named = included_names(res)

    def slim(item: dict) -> dict:
        a = _a(item)
        metric_id, profile_id = rel_id(item, "metric"), rel_id(item, "profile")
        out = {"id": item.get("id"), "datetime": a.get("datetime"),
               "metric_id": metric_id,
               "metric": _a(named.get(("metric", metric_id), {})).get("name"),
               "profile_id": profile_id,
               "profile_email": _a(named.get(("profile", profile_id), {})).get("email"),
               "properties": a.get("event_properties")}
        return {k: v for k, v in out.items() if v is not None}
    return slim


def _event_data(metric_name: str, profile: Dict[str, Any], properties: Optional[dict],
                **attrs: Any) -> dict:
    """The JSON:API event of `create_event`, from named arguments."""
    unknown = sorted(set(profile) - {"id", *PROFILE_ATTRIBUTES})
    if unknown:
        raise _bad(f"`profile`: Klaviyo does not take {unknown}; allowed: id, "
                   f"{', '.join(PROFILE_ATTRIBUTES)}.")
    if not profile.get("id") and not any(profile.get(k) for k in PROFILE_IDENTIFIERS):
        raise _bad("`profile` needs an id, email, phone_number or external_id.")
    who: Dict[str, Any] = {"type": "profile"}
    if profile.get("id"):
        who["id"] = profile["id"]
    rest = {k: v for k, v in profile.items() if k != "id"}
    if rest:
        who["attributes"] = rest
    body: Dict[str, Any] = {
        "properties": properties or {},
        "metric": {"data": {"type": "metric", "attributes": {"name": metric_name}}},
        "profile": {"data": who},
        **{k: v for k, v in attrs.items() if v is not None and v is not False}}
    return {"type": "event", "attributes": body}


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=LECTURE)
    def klaviyo_campaigns(
        op: Literal["list", "get"] = "list",
        campaign_id: Optional[str] = None,
        channel: Literal["email", "sms", "mobile_push"] = "email",
        filter: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        include: Optional[List[Literal["campaign-messages", "tags"]]] = None,
        full: bool = False,
    ) -> dict:
        """Klaviyo campaigns (read only — this connector never sends, creates or
        edits a campaign).

        `op`:
        - `list` (default) — campaigns of ONE `channel`: `{campaigns: [{id, name,
          status, archived, audiences, created_at, scheduled_at, updated_at,
          send_time}], count, next_cursor, omitted}`.
        - `get` — one campaign (`campaign_id`) with all its attributes;
          `include=["campaign-messages"]` adds its `messages` (subject, content).

        For opens, clicks or revenue: `klaviyo_reports(op="campaigns")`.

        Args:
            op: list | get.
            campaign_id: get — Klaviyo campaign id.
            channel: list — email (default) | sms | mobile_push.
            filter: list — extra clauses, comma-joined: `equals(status,'Sent')`,
                `greater-or-equal(scheduled_at,2026-09-01T00:00:00Z)`,
                `contains(name,'Promo')`, `equals(archived,false)`.
            sort: list — created_at | id | name | scheduled_at | updated_at,
                `-` prefix descends.
            page_size: list — 1-100, default 100.
            page_cursor: list — `next_cursor` of the previous page.
            all_pages: list — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            include: campaign-messages, tags.
            full: raw JSON:API payload.
        """
        if op == "list":
            hors_op(op, (*_LIST_OPS, "include"), campaign_id=campaign_id)
            if filter and "messages.channel" in filter:
                raise _bad("the channel goes in `channel`, not in `filter`.")
            flt = f"equals(messages.channel,'{channel}')" + (f",{filter}" if filter else "")
            walk = pages(all_pages, max_pages)
            res = run(lambda: _client().list_campaigns(
                flt, sort=sort, page_size=page_size, page_cursor=page_cursor,
                include=include, **walk), "campaigns:read")
            return res if full else listing(res, "campaigns", _slim_campaign,
                                            _CAMPAIGN_OMITTED, all_pages=all_pages)
        if op == "get":
            need(op, campaign_id=campaign_id)
            hors_op(op, ("campaign_id", "include", "full"), filter=filter, sort=sort,
                    page_size=page_size, page_cursor=page_cursor, all_pages=all_pages,
                    max_pages=max_pages)
            res = run(lambda: _client().get_campaign(campaign_id, include=include),
                      "campaigns:read")
            return res if full else _campaign_record(res)
        raise _bad(f"invalid `op`: {op!r} (expected: list | get).")

    @mcp.tool(annotations=LECTURE)
    def klaviyo_flows(
        op: Literal["list", "get"] = "list",
        flow_id: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        definition: bool = False,
        full: bool = False,
    ) -> dict:
        """Klaviyo flows — automations (read only: never created or edited here).

        `op`:
        - `list` (default) — `{flows: [{id, name, status (draft | manual | live),
          archived, trigger_type, created, updated}], count, next_cursor}`, at most
          50 per page.
        - `get` — one flow (`flow_id`); `definition=True` adds its triggers,
          actions and branches.

        Args:
            op: list | get.
            flow_id: get — Klaviyo flow id.
            filter: list — `equals(status,'live')`, `equals(trigger_type,'Added to
                List')` ('Metric', 'Date Based', 'Price Drop'…), `contains(name,'x')`.
            sort: list — created | id | name | status | trigger_type | updated,
                `-` prefix descends.
            page_size: list — 1-50, default 50.
            page_cursor: list — `next_cursor` of the previous page.
            all_pages: list — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            definition: get — add the flow definition.
            full: raw JSON:API payload.
        """
        if op == "list":
            hors_op(op, _LIST_OPS, flow_id=flow_id, definition=definition)
            walk = pages(all_pages, max_pages)
            res = run(lambda: _client().list_flows(
                filter=filter, sort=sort, page_size=page_size, page_cursor=page_cursor,
                **walk), "flows:read")
            return res if full else listing(res, "flows", _slim_flow, all_pages=all_pages)
        if op == "get":
            need(op, flow_id=flow_id)
            hors_op(op, ("flow_id", "definition", "full"), filter=filter, sort=sort,
                    page_size=page_size, page_cursor=page_cursor, all_pages=all_pages,
                    max_pages=max_pages)
            res = run(lambda: _client().get_flow(
                flow_id, additional_fields=["definition"] if definition else None),
                "flows:read")
            return res if full else _slim_flow(res.get("data") or {})
        raise _bad(f"invalid `op`: {op!r} (expected: list | get).")

    @mcp.tool(annotations=LECTURE)
    def klaviyo_metrics(
        op: Literal["list", "get"] = "list",
        metric_id: Optional[str] = None,
        filter: Optional[str] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        full: bool = False,
    ) -> dict:
        """Klaviyo metrics — the event types (Placed Order, Opened Email…), read only.

        `op`:
        - `list` (default) — `{metrics: [{id, name, created, updated, integration,
          integration_category}], count, next_cursor}`. The id (6 characters) is
          what `klaviyo_events` filters on, and what the reports take as
          `metric_id` / `conversion_metric_id` (usually Placed Order).
        - `get` — one metric (`metric_id`).

        Args:
            op: list | get.
            metric_id: get — Klaviyo metric id.
            filter: list — `equals(integration.name,'Shopify')`,
                `equals(integration.category,'…')`.
            page_cursor: list — `next_cursor` of the previous page.
            all_pages: list — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            full: raw JSON:API payload.
        """
        if op == "list":
            hors_op(op, ("filter", "page_cursor", "all_pages", "max_pages", "full"),
                    metric_id=metric_id)
            walk = pages(all_pages, max_pages)
            res = run(lambda: _client().list_metrics(
                filter=filter, page_cursor=page_cursor, **walk), "metrics:read")
            return res if full else listing(res, "metrics", _slim_metric,
                                            all_pages=all_pages)
        if op == "get":
            need(op, metric_id=metric_id)
            hors_op(op, ("metric_id", "full"), filter=filter, page_cursor=page_cursor,
                    all_pages=all_pages, max_pages=max_pages)
            res = run(lambda: _client().get_metric(metric_id), "metrics:read")
            return res if full else _slim_metric(res.get("data") or {})
        raise _bad(f"invalid `op`: {op!r} (expected: list | get).")

    @mcp.tool()
    def klaviyo_events(
        op: Literal["list", "create"] = "list",
        filter: Optional[str] = None,
        sort: Optional[Literal["datetime", "-datetime", "timestamp", "-timestamp"]] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        include: Optional[List[Literal["metric", "profile"]]] = None,
        metric_name: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
        properties: Optional[Dict[str, Any]] = None,
        time: Optional[str] = None,
        value: Optional[float] = None,
        value_currency: Optional[str] = None,
        unique_id: Optional[str] = None,
        backfill: bool = False,
        confirm: bool = False,
        full: bool = False,
    ) -> dict:
        """Klaviyo events — something a profile did at a time (an order, an open…).

        `op`:
        - `list` (default) — `{events: [{id, datetime, metric_id, metric?,
          profile_id, profile_email?, properties}], count, next_cursor}`;
          `include` names the metric and the profile. Filter by metric and
          profile and a datetime range rather than paging the whole account.
        - `create` — ⚠️ SENSITIVE: record an event for `profile`, under the
          metric `metric_name` (created if new). Flows triggered by this metric
          START for the profile and may send messages — unless `backfill=True`
          (a past event, no flow). Without `confirm=True` it only returns a
          preview. Confirmed, Klaviyo queues it (HTTP 202): `{queued: true}`,
          not yet visible in `list`.

        Args:
            op: list | create.
            filter: list — `equals(metric_id,"UxxK4u")`, `equals(profile_id,"01H…")`,
                `greater-or-equal(datetime,2026-09-01T00:00:00Z)`, comma-joined.
            sort: list — datetime | -datetime | timestamp | -timestamp.
            page_size: list — 1-1000, default 200.
            page_cursor: list — `next_cursor` of the previous page.
            all_pages: list — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            include: list — metric, profile.
            metric_name: create — e.g. "Placed Order".
            profile: create — {id} or {email | phone_number | external_id, plus
                profile attributes to set}.
            properties: create — event properties, e.g. {order_id, items}.
            time: create — ISO 8601, when it happened (now by default).
            value: create — monetary value, e.g. the order amount.
            value_currency: create — ISO 4217 code, e.g. EUR.
            unique_id: create — makes a retry idempotent.
            backfill: create — record a past event WITHOUT starting flows.
            confirm: create — True to really record it (after the preview).
            full: list — raw JSON:API payload.
        """
        if op == "list":
            hors_op(op, (*_LIST_OPS, "include"), metric_name=metric_name,
                    profile=profile, properties=properties, time=time, value=value,
                    value_currency=value_currency, unique_id=unique_id,
                    backfill=backfill, confirm=confirm)
            walk = pages(all_pages, max_pages)
            res = run(lambda: _client().list_events(
                filter=filter, sort=sort, page_size=page_size, page_cursor=page_cursor,
                include=include, **walk), "events:read")
            return res if full else listing(res, "events", _event_slimmer(res),
                                            ("uuid", "timestamp"), all_pages=all_pages)
        if op == "create":
            need(op, metric_name=metric_name, profile=profile)
            hors_op(op, ("confirm",), filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, all_pages=all_pages, max_pages=max_pages,
                    include=include, full=full)
            data = _event_data(metric_name, profile, properties, time=time, value=value,
                               value_currency=value_currency, unique_id=unique_id,
                               backfill=backfill)
            if not confirm:
                effect = ("backfill: recorded as a past event, no flow starts." if backfill
                          else "Flows triggered by this metric START for this profile "
                               "and may send messages. Pass backfill=True for a past "
                               "event that must not trigger anything.")
                return preview("create_event", effect, metric=metric_name,
                               profile=profile, backfill=backfill)
            run(lambda: _client().create_event(data), "events:write")
            return {"queued": True, "metric": metric_name, "backfill": backfill,
                    "note": "HTTP 202: Klaviyo queued the event; it is not yet "
                            "visible in op='list'."}
        raise _bad(f"invalid `op`: {op!r} (expected: list | create).")

    @mcp.tool(annotations=LECTURE)
    def klaviyo_reports(
        op: Literal["campaigns", "flows", "metric"],
        statistics: Optional[List[str]] = None,
        conversion_metric_id: Optional[str] = None,
        timeframe: Optional[_TIMEFRAMES] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        group_by: Optional[List[str]] = None,
        filter: Optional[str] = None,
        metric_id: Optional[str] = None,
        measurements: Optional[List[Literal["count", "sum_value", "unique"]]] = None,
        interval: Optional[Literal["hour", "day", "week", "month"]] = None,
        by: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Klaviyo performance figures (read only).

        `op`:
        - `campaigns` / `flows` — the figures of Klaviyo's reports, per campaign
          (or flow) message: `{results: [{groupings: {campaign_id | flow_id,
          …_message_id, send_channel}, statistics: {…}}], next_cursor}`. Rates are
          fractions (0-1). Needs `statistics`, `conversion_metric_id` (the Placed
          Order metric, from `klaviyo_metrics`, required even without conversion
          figures) and `timeframe` OR `since`+`until` (≤ 1 year). ⚠️ Klaviyo allows
          2 calls per minute and 225 per day: ask every statistic in ONE call.
        - `metric` — aggregate one metric's events (`metric_id`, `measurements`)
          between `since` (included) and `until` (excluded), per `interval`:
          `{dates: [...], data: [{dimensions, measurements: {count: [...]…}}]}`.

        Args:
            op: campaigns | flows | metric.
            statistics: campaigns / flows — e.g. recipients, delivered, opens_unique,
                open_rate, clicks_unique, click_rate, conversions, conversion_value,
                conversion_rate, revenue_per_recipient, unsubscribes,
                unsubscribe_rate, bounced, spam_complaints.
            conversion_metric_id: campaigns / flows — metric id of Placed Order.
            timeframe: campaigns / flows — a named period (last_30_days…).
            since: ISO 8601 start (campaigns / flows: instead of timeframe).
            until: ISO 8601 end (metric: excluded).
            group_by: campaigns / flows — must keep campaign_id + campaign_message_id
                (flow_id + flow_message_id); add send_channel, tag_name, variation…
            filter: clauses comma-joined — campaigns: `equals(send_channel,"email")`;
                flows: `equals(flow_id,"Ab12Cd")`; metric: `equals($attributed_flow,"Ab12Cd")`.
            metric_id: metric — Klaviyo metric id.
            measurements: metric — count | sum_value | unique.
            interval: metric — hour | day (default) | week | month.
            by: metric — dimensions, e.g. $attributed_flow, $message, Campaign Name.
            timezone: metric — IANA, e.g. Europe/Paris (default UTC).
            sort: metric — a `by` dimension, `-` prefix descends.
            page_size: metric — rows per page, default 500.
            page_cursor: `next_cursor` of the previous page.
            full: raw JSON:API payload.
        """
        if op in ("campaigns", "flows"):
            need(op, statistics=statistics, conversion_metric_id=conversion_metric_id)
            hors_op(op, (), metric_id=metric_id, measurements=measurements,
                    interval=interval, by=by, timezone=timezone, sort=sort,
                    page_size=page_size)
            if bool(timeframe) == bool(since or until):
                raise _bad("pass `timeframe` OR `since` + `until`, exactly one.")
            if not timeframe and not (since and until):
                raise _bad("a custom period needs both `since` and `until`.")
            attrs: Dict[str, Any] = {
                "statistics": statistics, "conversion_metric_id": conversion_metric_id,
                "timeframe": {"key": timeframe} if timeframe else {"start": since, "end": until}}
            if group_by:
                attrs["group_by"] = group_by
            if filter:
                attrs["filter"] = filter
            kind = "campaign-values-report" if op == "campaigns" else "flow-values-report"
            data = {"type": kind, "attributes": attrs}
            if op == "campaigns":
                res = run(lambda: _client().query_campaign_values(
                    data, page_cursor=page_cursor), "campaigns:read")
            else:
                res = run(lambda: _client().query_flow_values(
                    data, page_cursor=page_cursor), "flows:read")
        elif op == "metric":
            need(op, metric_id=metric_id, measurements=measurements, since=since,
                 until=until)
            hors_op(op, (), statistics=statistics,
                    conversion_metric_id=conversion_metric_id, timeframe=timeframe,
                    group_by=group_by)
            attrs = {"metric_id": metric_id, "measurements": measurements,
                     "filter": [f"greater-or-equal(datetime,{since})",
                                f"less-than(datetime,{until})", *split_clauses(filter)]}
            for key, val in (("interval", interval), ("by", by), ("timezone", timezone),
                             ("sort", sort), ("page_size", page_size),
                             ("page_cursor", page_cursor)):
                if val is not None:
                    attrs[key] = val
            data = {"type": "metric-aggregate", "attributes": attrs}
            res = run(lambda: _client().query_metric_aggregates(data), "metrics:read")
        else:
            raise _bad(f"invalid `op`: {op!r} (expected: campaigns | flows | metric).")
        if full:
            return res
        from oto.tools.klaviyo import next_cursor

        a = _a(res.get("data") or {})
        out: Dict[str, Any] = ({"results": a.get("results") or []} if op != "metric"
                               else {"dates": a.get("dates") or [], "data": a.get("data") or []})
        out["next_cursor"] = next_cursor(res)
        return out
