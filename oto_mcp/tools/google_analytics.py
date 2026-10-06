"""Google Analytics 4 — read access via a SERVICE ACCOUNT KEY: properties,
reports, realtime, catalogue of dimensions and metrics, key events.

Wraps `oto.tools.google_analytics.GA4Client` (Admin + Data API v1beta).
Credential with ONE secret field (`secret_kind="fields"`, resolved by
`access.resolve_credential_fields`): `service_account_json`, the key's JSON
file, whole. Not the OAuth of the `google` connector: a user's consent to the
`analytics.readonly` scope is blocked by Google for our
application (see `providers/google_analytics.py`).

**Five tools, read-only** — the client has no write method, and the
requested scope (`analytics.readonly`) would forbid it:

- `ga4_properties` — accounts and properties the service account sees (and,
  on request, their data streams);
- `ga4_report` — `:runReport`, the last 30 full days by default;
- `ga4_realtime` — `:runRealtimeReport`;
- `ga4_metadata` — the dimensions and metrics usable on a property;
- `ga4_key_events` — the configured key events.

**Two named refusals**, because these are the two likely mistakes:
- an invalid dimension or metric name (400 `INVALID_ARGUMENT`) → Google's
  message, which names the offending field, plus a pointer to `ga4_metadata`;
- a service account without access to the property (403) → the service
  account's email, to add as a Viewer in GA4.

**Projection**: a raw GA4 report repeats each column's name and wraps
each cell (`{"value": "12"}`); the default view is a TABLE (`columns` +
`rows`, typed metrics) that keeps the reliability warnings (sampling,
thresholds). A property's catalogue exceeds 450 entries (measured on 25/09/2026):
its default view returns the API names grouped by category, the detail of an entry
is requested via `search`. `full=True` returns the raw response everywhere.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

_CONNECTOR = "google_analytics"
_FIELD = "service_account_json"
_DEFAULT_LIMIT = 100
# `include_streams` makes one call per property: beyond that, we refuse rather than
# make the agent wait on dozens of calls it did not see coming.
_MAX_STREAM_PROPERTIES = 25
_METADATA_HINT = ("check the names with `ga4_metadata` (API names like `activeUsers`, "
                  "`eventName` — not the GA4 interface labels)")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """The upstream refusal, translated into guidance. Read from the refusal's CLASS (the
    canonical Google status, classified by the client), never from the text."""
    from oto.tools.google_analytics import (GA4InvalidArgument, GA4PermissionDenied,
                                            GA4ServiceDisabled, ServiceAccountAuthError)
    if isinstance(e, GA4InvalidArgument):
        return (f"GA4 refused the request: {e.message.strip()} — {_METADATA_HINT}. "
                "Some dimension × metric combinations are also incompatible.")
    if isinstance(e, GA4PermissionDenied):
        quoi = e.resource or "this resource"
        return (f"The service account {e.client_email} does not have access to {quoi}. Add "
                "this email as a Viewer of the property in GA4 (Admin → "
                "Property access management); `ga4_properties` lists what it "
                "already sees.")
    if isinstance(e, GA4ServiceDisabled):
        return ("A Google Analytics API is not enabled in the service account's "
                "Google Cloud project: enable \"Google Analytics Data API\" and \"Google "
                "Analytics Admin API\" (Google Cloud console → APIs & Services), then "
                f"retry. Google detail: {e.message}")
    if isinstance(e, ServiceAccountAuthError):
        return (f"Google refuses to issue a token for this service account key "
                f"({e.body}): key deleted or revoked, or service account disabled "
                "— an administrator must upload a new JSON key.")
    status = e.status_code
    if status == 429:
        return ("GA4: the property's request quota is reached (429) — retry later, "
                "or reduce the size of the reports.")
    if status >= 500:
        return f"GA4 is temporarily unavailable (HTTP {status}) — retry later."
    return f"GA4 refused the request (HTTP {status}): {getattr(e, 'message', '') or e.body}"


def _count_properties(summaries: list) -> int:
    return sum(len(a.get("propertySummaries") or ()) for a in summaries)


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001
    """"Test the connection" probe: `accountSummaries`, with no side effect.

    Returns WHO the key authenticates (the service account's email) and what it sees
    (accounts, properties). ⚠️ Zero visible properties is a REFUSAL, not a green: the
    key is valid but the connector can read nothing — the case of a service
    account that was forgotten as a Viewer in GA4."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.google_analytics import GA4Client

    try:
        client = GA4Client(fields[_FIELD])
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e)) from None
    try:
        summaries = client.account_summaries()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(_upstream_message(e)) from None
        raise
    n = _count_properties(summaries)
    if n == 0:
        raise connector_verify.NonAutorise(
            f"The key authenticates ({client.client_email}) but sees no GA4 property: "
            "add this email as a Viewer of the property in GA4 "
            "(Admin → Property access management).")
    return {"identity": {"service_account": client.client_email,
                         "accounts": len(summaries), "properties": n}}


def _compact_stream(s: dict) -> dict:
    web = s.get("webStreamData") or {}
    app = s.get("androidAppStreamData") or s.get("iosAppStreamData") or {}
    out = {"stream": s.get("name"), "type": s.get("type"), "name": s.get("displayName"),
           "measurement_id": web.get("measurementId"), "url": web.get("defaultUri"),
           "app": app.get("packageName") or app.get("bundleId")}
    return {k: v for k, v in out.items() if v}


def _compact_meta_entry(entry: dict) -> dict:
    out = {"api_name": entry.get("apiName"), "ui_name": entry.get("uiName"),
           "category": entry.get("category"), "type": entry.get("type"),
           "description": entry.get("description"),
           "custom": entry.get("customDefinition") or None,
           "expression": entry.get("expression")}
    return {k: v for k, v in out.items() if v}


def _by_category(entries: list) -> dict:
    grouped: dict[str, list] = {}
    for e in entries:
        grouped.setdefault(e.get("category") or "(no category)", []).append(e.get("apiName"))
    return grouped


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.google_analytics import GA4Client, flatten_report

    connector_verify.register(_CONNECTOR, _verify)

    def _client() -> GA4Client:
        return GA4Client(access.resolve_credential_fields(_CONNECTOR)[_FIELD])

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _table(resp: dict, prop: str, **echo) -> dict:
        out = {"property": prop, **{k: v for k, v in echo.items() if v is not None},
               **flatten_report(resp)}
        out["projection"] = ("table view (columns = dimensions then metrics); "
                             "full=True returns GA4's raw response")
        return out

    @mcp.tool()
    def ga4_properties(include_streams: bool = False, full: bool = False) -> dict:
        """The Google Analytics accounts and GA4 properties this connector can read
        — call it first: every other `ga4_*` tool needs a `property` id from here.

        Only properties where the service account was added as a user (Viewer is
        enough) are listed. A property you expect but don't see = that access is
        missing in GA4 (Admin → Property access management).

        Args:
            include_streams: also list each property's data streams (web/app,
                measurement id `G-…`, site URL) — one extra call per property,
                refused above 25 properties.
            full: raw `accountSummaries` (and raw streams) instead of the compact view.
        """
        def _go():
            client = _client()
            summaries = client.account_summaries()
            n = _count_properties(summaries)
            if include_streams and n > _MAX_STREAM_PROPERTIES:
                raise ValueError(
                    f"{n} visible properties: include_streams would make {n} calls. "
                    "List the properties first, then target the one you are interested in.")
            if full:
                out = {"accountSummaries": summaries, "property_count": n}
                if include_streams:
                    out["dataStreams"] = {
                        p["property"]: client.list_data_streams(p["property"])
                        for a in summaries for p in a.get("propertySummaries") or ()}
                return out
            accounts = []
            for a in summaries:
                props = []
                for p in a.get("propertySummaries") or ():
                    item = {"property": p.get("property"), "name": p.get("displayName")}
                    if p.get("propertyType") not in (None, "PROPERTY_TYPE_ORDINARY"):
                        item["type"] = p["propertyType"]
                    if include_streams:
                        item["streams"] = [_compact_stream(s) for s in
                                           client.list_data_streams(p["property"])]
                    props.append(item)
                accounts.append({"account": a.get("account"), "name": a.get("displayName"),
                                 "properties": props})
            return {"accounts": accounts, "property_count": n}
        return _run(_go)

    @mcp.tool()
    def ga4_report(
        property: str,
        metrics: list[str],
        dimensions: Optional[list[str]] = None,
        start_date: str = "30daysAgo",
        end_date: str = "yesterday",
        dimension_filter: Optional[dict] = None,
        metric_filter: Optional[dict] = None,
        order_by: Optional[list[str]] = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        full: bool = False,
    ) -> dict:
        """A GA4 report (Data API `runReport`) on one property: metrics broken down
        by dimensions over a date range. Default range = the last 30 complete days
        (`30daysAgo` → `yesterday`, today excluded — same as GA4's "Last 30 days").

        Use API names, not UI labels — `ga4_metadata` lists what the property
        supports (custom dimensions included, e.g. `customEvent:plan`). Common:
        metrics `activeUsers`, `sessions`, `screenPageViews`, `eventCount`,
        `keyEvents`, `engagementRate`, `totalRevenue`; dimensions `date`,
        `eventName`, `pagePath`, `country`, `deviceCategory`,
        `sessionDefaultChannelGroup`, `sessionSource`.

        Returns a table: `columns` (dimensions then metrics), `rows`, and
        `row_count` = total matching rows (page with `offset`). `metadata` carries
        GA4's reliability flags — `samplingMetadatas` (sampled),
        `subjectToThresholding` (small counts hidden for privacy),
        `dataLossFromOtherRow` (rare values folded into "(other)"): report them
        with the numbers, never silently.

        Args:
            property: GA4 property id, `123456789` or `properties/123456789` (from
                `ga4_properties`) — not a `G-…` measurement id.
            metrics: metric API names, e.g. ["activeUsers", "sessions"].
            dimensions: dimension API names, e.g. ["date", "eventName"].
            start_date / end_date: `YYYY-MM-DD`, `today`, `yesterday` or `NdaysAgo`.
            dimension_filter: simple form `{"eventName": "purchase", "country":
                ["France", "Belgium"]}` (text = exact match, list = any of,
                combined with AND), or a full GA4 FilterExpression (`andGroup`,
                `orGroup`, `notExpression`, `filter`) for other operators.
            metric_filter: same forms, on metrics (a number = equality; use a
                FilterExpression with `numericFilter` for > / <).
            order_by: e.g. ["-sessions", "date"] — `-` = descending.
            limit: max rows returned (default 100).
            offset: rows to skip, for paging.
            full: raw GA4 response instead of the table.
        """
        def _go():
            resp = _client().run_report(
                property, metrics=metrics, dimensions=dimensions, start_date=start_date,
                end_date=end_date, dimension_filter=dimension_filter,
                metric_filter=metric_filter, order_by=order_by, limit=limit,
                offset=offset or None)
            if full:
                return resp
            out = _table(resp, property, date_range=f"{start_date} → {end_date}")
            if out["row_count"] > offset + len(out["rows"]):
                out["next_offset"] = offset + len(out["rows"])
            return out
        return _run(_go)

    @mcp.tool()
    def ga4_realtime(
        property: str,
        metrics: Optional[list[str]] = None,
        dimensions: Optional[list[str]] = None,
        dimension_filter: Optional[dict] = None,
        metric_filter: Optional[dict] = None,
        order_by: Optional[list[str]] = None,
        limit: int = _DEFAULT_LIMIT,
        minutes_ago: Optional[int] = None,
        full: bool = False,
    ) -> dict:
        """What is happening right now on a GA4 property (Data API
        `runRealtimeReport`) — the last 30 minutes (60 on GA4 360).

        Realtime supports a SMALLER set of names than `ga4_report`: metrics
        `activeUsers`, `eventCount`, `screenPageViews`, `keyEvents`; dimensions
        such as `country`, `city`, `deviceCategory`, `platform`, `eventName`,
        `unifiedScreenName`, `minutesAgo`. No rows = nobody active in the window,
        not an error.

        Args:
            property: GA4 property id (from `ga4_properties`).
            metrics: default ["activeUsers"].
            dimensions: e.g. ["country"].
            dimension_filter / metric_filter / order_by: same forms as `ga4_report`.
            limit: max rows returned (default 100).
            minutes_ago: narrow the window to the last N minutes.
            full: raw GA4 response instead of the table.
        """
        def _go():
            resp = _client().run_realtime_report(
                property, metrics=metrics or ["activeUsers"], dimensions=dimensions,
                dimension_filter=dimension_filter, metric_filter=metric_filter,
                order_by=order_by, limit=limit, minutes_ago=minutes_ago)
            if full:
                return resp
            return _table(resp, property, window_minutes=minutes_ago)
        return _run(_go)

    @mcp.tool()
    def ga4_metadata(
        property: str,
        kind: Literal["all", "dimensions", "metrics"] = "all",
        search: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """The dimensions and metrics a GA4 property supports — the vocabulary of
        `ga4_report`, custom definitions included (`customEvent:…`, `customUser:…`).
        Check here before building a report, or when one is refused for an
        invalid name.

        Default view: API names grouped by category (a property has 450+
        entries). Pass `search` to get the matching entries in detail (UI name,
        description, metric type), or `full=True` for everything raw.

        Args:
            property: GA4 property id (from `ga4_properties`); `0` = the catalogue
                common to all properties (no custom definitions).
            kind: "all" (default) | "dimensions" | "metrics".
            search: case-insensitive match on API name, UI name or description.
            full: raw `/metadata` response.
        """
        def _go():
            meta = _client().get_metadata(property)
            parts = ("dimensions", "metrics") if kind == "all" else (kind,)
            if full:
                return {p: meta.get(p) or [] for p in parts}
            if search:
                needle = search.lower()
                out = {}
                for p in parts:
                    hits = [e for e in meta.get(p) or ()
                            if needle in " ".join(str(e.get(k) or "") for k in
                                                  ("apiName", "uiName", "description")).lower()]
                    out[p] = [_compact_meta_entry(e) for e in hits]
                return out
            out = {p: _by_category(meta.get(p) or []) for p in parts}
            out["counts"] = {p: len(meta.get(p) or ()) for p in parts}
            out["projection"] = ("API names by category; `search` returns the detail of "
                                 "the matching entries, full=True the raw response")
            return out
        return _run(_go)

    @mcp.tool()
    def ga4_key_events(property: str, full: bool = False) -> dict:
        """The key events (formerly "conversions") configured on a GA4 property —
        the events whose counts `ga4_report` reports under the `keyEvents` metric.

        Args:
            property: GA4 property id (from `ga4_properties`).
            full: raw `keyEvents` records.
        """
        def _go():
            events = _client().list_key_events(property)
            if full:
                return {"keyEvents": events}
            rows = []
            for e in events:
                item = {"event_name": e.get("eventName"),
                        "counting_method": e.get("countingMethod"),
                        "created": e.get("createTime"), "custom": e.get("custom"),
                        "default_value": e.get("defaultValue")}
                rows.append({k: v for k, v in item.items() if v is not None})
            return {"key_events": rows, "count": len(rows)}
        return _run(_go)
