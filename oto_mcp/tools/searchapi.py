"""SearchApi — multi-engine search via SearchApi.io (full scope of the API).

Wraps the **SearchApi.io** REST API (`GET https://www.searchapi.io/api/v1/search`,
a single endpoint parameterized by `engine`). **Consolidated surface (ADR 0047
§Amendment applied to a connector)**: since the API has only ONE endpoint, it
exposes only ONE tool — `searchapi_search`, the vertical chosen by `engine`.
The 6 typed tools from before (`searchapi_{web,news,jobs,scholar,maps,youtube}_search`)
differed from the generic one only by a hardcoded `engine` and by named
fields (`q`/`gl`/`hl`/`location`/`num`/`page`, common to most engines):
they have become `engine` values + typed parameters of the generic tool.
`engine` stays **open** (any SearchApi id, including an engine this
module does not know) — that is the connector's very capability, we do not close it
with an allowlist.

No oto-core dependency: the HTTP client is **self-contained** (httpx), like
`infosec`/`fr`. Key resolved per call via `access.resolve_api_key("searchapi")`:
user key (`/account`) or the org's shared credential if set, otherwise platform key
+ daily quota for members (same regime as serper/serpapi). Why in addition
to serper/serpapi: SearchApi has its own engine coverage + parsing, useful
as a fallback or when a SearchApi key is already in place on the customer side.
"""
from __future__ import annotations

from typing import Optional

import httpx
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, url_perimeter
from ..connectors import verify as connector_verify

_BASE_URL = "https://www.searchapi.io/api/v1/search"
_ACCOUNT_URL = "https://www.searchapi.io/api/v1/me"
_TIMEOUT = 30.0


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /api/v1/me` — dedicated "account usage" endpoint (remaining credits,
    hourly limit), documented as "without requiring a specific plan level":
    free, unlike `/api/v1/search` (billed per request, which is what
    this module wraps). Bearer header (never in the query — this module's `_run`
    already applies that rule on `/search`, see #284).

    **Authenticated ≠ usable** (class oto#69): does not read the balance (no
    field shape confirmed in the time available) — `auth` only.
    """
    import requests

    r = requests.get(_ACCOUNT_URL, headers={"Authorization": f"Bearer {fields['key']}"},
                     timeout=15)
    r.raise_for_status()


def register(mcp: FastMCP) -> None:
    connector_verify.register("searchapi", _verify)


    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _run(engine: str, params: dict) -> dict:
        """Resolve the key, call SearchApi, count platform usage.

        The key goes in `Authorization: Bearer` (never in the query — no leak
        into access logs). An upstream 4xx (input rejected) surfaces as-is via
        `raise_for_status`; Sentry drops third-party 4xx (see CLAUDE.md).
        """
        key, is_platform = access.resolve_api_key("searchapi")
        payload = {k: v for k, v in params.items() if v is not None}
        payload["engine"] = engine
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(_BASE_URL, params=payload,
                      headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            data = r.json()
        if is_platform:
            access.record_platform_usage("searchapi")
        return data

    @mcp.tool()
    def searchapi_search(
        engine: str,
        query: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        location: Optional[str] = None,
        num: Optional[int] = None,
        page: Optional[int] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """SearchApi.io call — ONE endpoint, the vertical picked by `engine`.

        Reaches ANY SearchApi engine: pass the engine id plus either the typed
        fields below (the params common to most engines) or `params` for
        anything engine-specific. Returns the raw SearchApi JSON payload. Under
        a project with `excluded_url_prefixes`, matching results are dropped and
        counted.

        Engines — common ids (any SearchApi engine id is accepted, this list is
        not a closed set):
            Google verticals: google, google_news, google_maps, google_jobs,
            google_scholar, google_images, google_videos, google_shopping,
            google_trends, google_lens, google_autocomplete, google_finance,
            google_play, google_events, google_flights, google_hotels.
            Other engines: youtube, youtube_transcripts, bing, bing_news,
            baidu, duckduckgo, yahoo, yandex, amazon_search, ebay_search,
            walmart_search, apple_app_store.
        See searchapi.io docs for each engine's parameters.

        Flagship verticals — what each one is and what it returns:
        - engine="google": Google web search. Returns 'organic_results'.
          Takes query, country, language, location, num, page.
        - engine="google_news": Google News search — recent news for a query.
          Returns 'organic_results' (news articles with source, date, link).
          Takes query, country, language.
        - engine="google_jobs": Google Jobs search — live job postings
          (job-board sourcing). Returns 'jobs' (each with title, company,
          location, apply options). Takes query, location, country, language.
        - engine="google_scholar": Google Scholar search — academic papers /
          citations for a query. Returns 'organic_results' (title, authors,
          publication, citations). Takes query, language.
        - engine="google_maps": Google Maps search — local places/businesses
          for a query. Returns 'local_results' (name, address, phone, rating,
          coordinates). Takes query, location, language.
        - engine="youtube": YouTube search — videos, channels, playlists for a
          query. Returns 'videos' / 'channels' / 'playlists'. Takes query,
          country, language.

        Any other engine: use `params` for its own inputs, e.g.
        engine="google_lens" + params={"url": "https://…"}, or
        engine="google_flights" + params={"departure_id": "CDG", …}.
        The typed fields are the COMMON case, not a universal set: an engine
        that does not support one of them rejects the call upstream (4xx).

        Either `query` or `params` must be provided.

        Args:
            engine: SearchApi engine id (see the list above). Required — no
                default vertical is assumed.
            query: search query (SearchApi `q`).
            country: 2-letter country code (`gl`, e.g. "fr", "us").
            language: UI language (`hl`, e.g. "fr", "en").
            location: geographic location / anchor, e.g. "Paris, France".
            num: number of results per page.
            page: result page (1-based).
            params: engine-specific params, e.g. {"q": "pizza", "gl": "us",
                "hl": "en", "location": "Paris, France"}. Merged LAST: a key
                given here overrides the typed field it duplicates. Use it for
                engines whose input is not `q`, and for any filter without a
                typed field (time range, sorting, ids…).
        """
        if not engine or not engine.strip():
            raise _bad(
                "searchapi_search requires `engine` (the SearchApi vertical), "
                "e.g. 'google', 'google_news', 'google_jobs', 'google_scholar', "
                "'google_maps', 'youtube' — see the full list in the tool "
                "description. Any SearchApi engine id is accepted."
            )
        if query is None and not params:
            raise _bad(
                f"searchapi_search(engine='{engine}') requires `query` (the engine's "
                "`q`) — or `params` for an engine whose input is not `q` "
                "(e.g. engine='google_lens' with params={'url': …})."
            )
        payload: dict = {"q": query, "gl": country, "hl": language,
                         "location": location, "num": num, "page": page}
        if params:
            payload.update(params)
        return url_perimeter.filter_results(_run(engine, payload),
                                            url_perimeter.perimeter_of_call())
