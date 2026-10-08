"""Google Ads — oto-core surface (GoogleAdsClient) exposed per user, multi-account.

Eighth service of the Google account (2026-10-08): scope `adwords`, granted from its
card. Reads see the Google Ads accounts THEIR Google user can open, nothing more.

**Three tools, the surface of Google's own MCP server** (`googleads/google-ads-mcp`):
- `google_ads_customers` — the accounts the user opens directly;
- `google_ads_search` — one GAQL query;
- `google_ads_fields` — what a resource offers.

**Read-only is enforced by the client** (`oto.tools.google.ads`), not by Google:
`adwords` is the only scope Google publishes and it would allow mutating. The client
builds no mutate URL and refuses anything but a GAQL `SELECT … FROM …` before sending.

**No developer token** (sunset by Google on 2026-09-09): the access level is that of
the Google Cloud project owning the OAuth client. A project on Test access is refused
on real accounts — named refusal, an app setting, never the person's account.

**Paging**: Google pages by 10,000 rows (fixed since v19). The agent reads at most
`max_rows` (≤ 1,000, ~200 KB) per call; the returned `page_token` is OURS — Google's
page token + an offset inside its page — so nothing between two slices is lost.
Google's tokens expire after ~2 h.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import TYPE_CHECKING, Any, Callable, Optional

import requests
from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access
from ..auth import google as google_oauth
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation only — never evaluated at runtime
    from oto.tools.google.ads import GoogleAdsClient

_SERVICE = "google_ads"
# Each Google call (connect, read): under the 45 s of a REST invocation, token included.
_HTTP_TIMEOUT = (10, 20)
_TOKEN_TIMEOUT_S = 20
_DEFAULT_ROWS = 200
_MAX_ROWS = 1000
_MAX_OUT_BYTES = 200_000

# Error families (`errorCode` keys) that mean "the request itself is wrong": the
# query, a field, a date — the agent fixes its GAQL, nothing else would help.
_REQUEST_FAMILIES = frozenset({"queryError", "requestError", "fieldError",
                               "fieldMaskError", "dateError", "dateRangeError"})


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


# --- the client, per call ----------------------------------------------------

def _client_for_user(account: Optional[str] = None):
    from oto.tools.google.ads import GoogleAdsClient

    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service=_SERVICE)
    except RuntimeError as e:
        raise _bad(str(e))
    return GoogleAdsClient(creds.token, timeout=_HTTP_TIMEOUT)


async def _client(account: Optional[str] = None) -> GoogleAdsClient:
    """Off the event loop (vault read + possible refresh), with its own deadline."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_TOKEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _bad(f"Google did not respond within {_TOKEN_TIMEOUT_S}s "
                   "(token refresh) — try again.")


async def _run(fn: Callable[[], Any]) -> Any:
    """One client read, off the event loop; its refusals → what to do next."""
    from oto.tools.google.ads import GoogleAdsError

    try:
        return await asyncio.to_thread(fn)
    except GoogleAdsError as e:
        raise _api_error(e)
    except ValueError as e:
        raise _bad(str(e))
    except requests.Timeout:
        raise _bad(f"Google Ads did not answer within {_HTTP_TIMEOUT[1]}s — narrow the "
                   "query (shorter date range, LIMIT) and try again.")
    except requests.RequestException as e:
        raise _bad(f"Google Ads is unreachable ({type(e).__name__}) — try again later.")


def _api_error(e) -> McpError:
    """A `GoogleAdsError` → what to do next, read from the error CODE, not the text."""
    names, detail, http = e.codes, e.detail, e.status_code
    ref = f" (Google request id {e.request_id})" if e.request_id else ""
    if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in names:
        msg = ("This Google Ads connection is only approved for TEST accounts: the Google "
               "Cloud project of the OAuth app that issued it has the « Test » access "
               "level. Reading a real account needs Explorer, Basic or Standard access, "
               "requested by that app's administrator (Google Cloud console → Google Ads "
               "API → Overview). Reconnecting changes nothing.")
    elif "SERVICE_DISABLED" in names:
        msg = ("The Google Ads API is not enabled in the Google Cloud project of the OAuth "
               "app that issued the connection — a setting of that app, for its "
               "administrator (Google Cloud console → APIs & Services → Google Ads API); "
               f"reconnecting changes nothing. Google detail: {e.message}")
    elif "USER_PERMISSION_DENIED" in names:
        msg = ("Your Google user reaches this account through a MANAGER account: pass "
               "`login_customer_id` = the manager's 10-digit id (`google_ads_customers` "
               f"lists the accounts you open directly). Google detail: {detail}")
    elif "NOT_ADS_USER" in names:
        msg = ("This Google account is not a user of any Google Ads account — connect "
               "the Google account that has Google Ads access, or have it invited "
               "(Google Ads → Admin → Access and security).")
    elif "CUSTOMER_NOT_ENABLED" in names:
        msg = (f"This Google Ads account is not enabled (signup unfinished, or cancelled) "
               f"— nothing can be read from it. Google detail: {detail}")
    elif e.families & _REQUEST_FAMILIES or e.status == "INVALID_ARGUMENT":
        msg = (f"Google Ads refused the request: {detail} — check field names and "
               "compatibility with `google_ads_fields`, and the GAQL grammar "
               "(full field names, quoted string values, dates as 'YYYY-MM-DD').")
    elif http == 429 or e.status == "RESOURCE_EXHAUSTED":
        msg = f"Google Ads: quota or rate limit reached — try again later. Detail: {detail}"
    elif http == 401:
        msg = ("Google refused the access token for Google Ads — reconnect Google Ads "
               f"from its card. Google detail: {e.message}")
    elif http == 403:
        msg = (f"Your Google user has no access to this Google Ads account: {detail} — "
               "check the customer id, or pass the manager's `login_customer_id`.")
    elif http == 404:
        msg = f"Google Ads cannot find this resource: {detail}"
    elif http >= 500:
        msg = f"Google Ads is temporarily unavailable (HTTP {http}) — try again later."
    else:
        msg = f"Google Ads refused the request (HTTP {http}, {e.status or '?'}): {detail}"
    return _bad(msg + ref)


# --- our cursor over Google's 10,000-row pages -------------------------------

def _query_key(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def _encode_cursor(google_token: Optional[str], offset: int, query: str) -> str:
    raw = json.dumps({"g": google_token, "o": offset, "q": _query_key(query)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, query: str) -> tuple[Optional[str], int]:
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
        google_token, offset, key = data["g"], int(data["o"]), data["q"]
    except (ValueError, KeyError, TypeError):
        raise _bad("`page_token` is not one returned by `google_ads_search`.") from None
    if key != _query_key(query) or offset < 0:
        raise _bad("`page_token` belongs to another query: resend the SAME query "
                   "with it, or omit it to start over.")
    return google_token, offset


def _page(data: dict, query: str, google_token: Optional[str], offset: int,
          max_rows: int) -> dict:
    """A Google page → the slice returned to the agent, and the cursor that follows."""
    from oto.tools.google.ads import flatten_rows

    try:
        columns, table = flatten_rows(data)
    except ValueError as e:
        raise _bad(str(e))
    rows: list[list] = []
    size = 0
    truncated_bytes = False
    for line in table[offset:offset + max_rows]:
        size += len(json.dumps(line, default=str, ensure_ascii=False)) + 1
        if size > _MAX_OUT_BYTES and rows:
            truncated_bytes = True
            break
        rows.append(line)
    out: dict = {"columns": columns, "rows": rows, "row_count": len(rows)}
    if data.get("totalResultsCount") is not None:
        out["total_rows"] = int(data["totalResultsCount"])
    nxt = offset + len(rows)
    if nxt < len(table):
        out["page_token"] = _encode_cursor(google_token, nxt, query)
    elif data.get("nextPageToken"):
        out["page_token"] = _encode_cursor(data["nextPageToken"], 0, query)
    if truncated_bytes:
        out["truncated_bytes"] = True
        out["hint"] = (f"page cut at ~{_MAX_OUT_BYTES // 1000} KB — select fewer fields, "
                       "or aggregate (segments.date out, a coarser segment).")
    elif "page_token" in out:
        out["hint"] = ("more rows — call again with the SAME query and `page_token` "
                       "(valid ~2 h), or narrow with WHERE / LIMIT.")
    return out


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def google_ads_customers(account: Optional[str] = None) -> dict:
        """List the Google Ads accounts your Google user opens DIRECTLY (customer ids).

        Start here when no customer id is known. A manager account (MCC) appears as
        one id: its client accounts are read with `google_ads_search` on the manager —
        `SELECT customer_client.id, customer_client.descriptive_name,
        customer_client.manager, customer_client.status FROM customer_client` — then
        each client is queried with `login_customer_id` = the manager's id. An
        account's name, currency and time zone: `SELECT customer.descriptive_name,
        customer.currency_code, customer.time_zone FROM customer`.

        Returns {customer_ids: ["1234567890", …]}.

        Args:
            account: email of the Google account to use — same choice as `_account`.
        """
        client = await _client(account)
        return {"customer_ids": await _run(client.list_accessible_customers)}

    @mcp.tool()
    async def google_ads_search(
        customer_id: str,
        query: str,
        max_rows: int = _DEFAULT_ROWS,
        page_token: Optional[str] = None,
        login_customer_id: Optional[str] = None,
        account: Optional[str] = None,
    ) -> dict:
        """Run one read-only GAQL query (Google Ads Query Language) on a Google Ads account.

        GAQL is `SELECT <fields> FROM <resource> [WHERE …] [ORDER BY …] [LIMIT n]`:
        full field names (`campaign.name`, never `name`), no joins, no `*`. Look up
        what a resource offers with `google_ads_fields` — do not guess fields.
        Money is in MICROS of the account currency (`metrics.cost_micros` / 1e6).
        Dates are in the account time zone. The answer names fields in camelCase
        (`metrics.cost_micros` → column `metrics.costMicros`), 64-bit numbers (ids,
        clicks, micros) come as strings, and a field Google leaves out of a row is
        null. Typical queries —
        - campaigns over 30 days: `SELECT campaign.id, campaign.name,
          campaign.status, metrics.cost_micros, metrics.impressions, metrics.clicks,
          metrics.conversions, metrics.conversions_value FROM campaign WHERE
          segments.date DURING LAST_30_DAYS ORDER BY metrics.cost_micros DESC`
        - daily spend: add `segments.date` to the SELECT (one row per day);
        - ads: `SELECT ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type,
          ad_group_ad.ad.final_urls, ad_group_ad.status, metrics.clicks FROM
          ad_group_ad WHERE segments.date DURING LAST_7_DAYS`
        - keywords: `SELECT ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type, metrics.clicks, metrics.cost_micros
          FROM keyword_view WHERE segments.date DURING LAST_30_DAYS`
        - search terms: `… FROM search_term_view WHERE segments.date DURING
          LAST_7_DAYS`.
        Date ranges: `DURING LAST_7_DAYS | LAST_30_DAYS | THIS_MONTH | LAST_MONTH`,
        or `segments.date BETWEEN '2026-01-01' AND '2026-01-31'`. More:
        oto_guide op=read slug="google-ads-gaql".

        Returns {columns (the selected fields), rows (lists, in column order),
        row_count, total_rows, page_token?, hint?} — at most `max_rows` rows per
        call; more → call again with the SAME query and `page_token`.

        Args:
            customer_id: the Google Ads account, 10 digits (dashes accepted).
            query: the GAQL query (SELECT only).
            max_rows: rows returned in this call (default 200, max 1000).
            page_token: `page_token` of the previous call, same query.
            login_customer_id: the manager account (10 digits) through which you
                reach `customer_id`, when your access goes through a manager.
            account: email of the Google account to use — same choice as `_account`.
        """
        from oto.tools.google.ads import check_select, customer_id as normalize

        try:
            cid = normalize(customer_id)
            lcid = normalize(login_customer_id) if login_customer_id else None
            query = check_select(query)
        except ValueError as e:
            raise _bad(str(e))
        if max_rows < 1:
            raise _bad("max_rows must be ≥ 1.")
        google_token, offset = (_decode_cursor(page_token, query) if page_token
                                else (None, 0))
        client = await _client(account)
        data = await _run(lambda: client.search(cid, query, page_token=google_token,
                                                login_customer_id=lcid))
        return {"customer_id": cid,
                **_page(data, query, google_token, offset, min(max_rows, _MAX_ROWS))}

    @mcp.tool()
    async def google_ads_fields(resource: str, account: Optional[str] = None) -> dict:
        """What a Google Ads resource offers to GAQL: its fields, and the metrics,
        segments and related-resource fields selectable WITH it.

        Use it before writing a `google_ads_search` query on a resource you have not
        queried yet (`campaign`, `ad_group`, `ad_group_ad`, `keyword_view`,
        `search_term_view`, `customer`, `customer_client`…). The answer changes only
        with Google's API version: reuse it within a conversation.

        Returns {resource, attributes, metrics, segments, related, not_filterable,
        not_sortable} — every list holds full field names, the first four are the
        selectable ones.

        Args:
            resource: the resource name, snake_case (e.g. "campaign", "ad_group_ad").
            account: email of the Google account to use — same choice as `_account`.
        """
        client = await _client(account)
        try:
            return await _run(lambda: client.describe_resource(resource))
        except RuntimeError as e:
            raise _bad(str(e))
