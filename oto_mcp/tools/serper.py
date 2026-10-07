"""Serper — Google search (web, images, videos, news, places, maps, reviews,
shopping, scholar, patents, lens, autocomplete) + page scraping.

Key resolved per call via `access.resolve_api_key("serper")`: user key
(`/account`) if set, otherwise platform key + daily quota for members.
Guests must set their own key.
"""
from __future__ import annotations

import re
import threading
from typing import Literal, Optional

import requests

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INVALID_REQUEST

from .. import access, output_projection, session_org, url_perimeter
from .lecture import LECTURE
from ..connectors import verify as connector_verify
from . import cesures, images_base64, mail_obfuscation

# What the default removes from a Google results page (rendered by `full=True`). None
# of these keys is noise in the absolute — knowledge graph and sitelinks sometimes serve
# — but none serves an agent's current loop (title + link + snippet), and
# together they weigh ~34% of a response (measured: 3,105 chars for 5 results, of which 542
# knowledgeGraph, 328 relatedSearches, 635 sitelinks).
#
# It WAS an opt-in `compact=True`, out of caution inherited from `fr_get` projected by allowlist
# which had silently lost `liste_idcc` (oto-core#37). Measured since: an agent plugged
# directly into the MCP NEVER passes it — six `serper_search` with `query` alone on a
# sheet, six full responses —, and it has no reason to: it does not know the
# parameter exists before reading the schema, and nothing tells it that it matters.
# A guide cannot teach it either, the one that drives these agents names
# no tool (by choice: that is what protects it from renames). **A saving
# parameter you must know about to benefit from benefits no one** — on a
# real enrichment conversation, 6,800 tokens of tool outputs for 784 of
# prompt. The default served the rare case and made the general case pay: reversed.
# The caution of #37 does not apply here — these lists are a DENYLIST of named keys,
# not an allowlist: they cannot make an unforeseen field disappear.
_SEARCH_DROP = ("knowledgeGraph", "peopleAlsoAsk", "relatedSearches", "searchParameters")

_RESULT_DROP = ("sitelinks", "attributes", "imageUrl", "thumbnailUrl")

# The client methods that PAGINATE server-side: several Serper requests behind
# a single tool call. They return the sum in `credits_used` (oto-core >= 1.121.0,
# = the pin); an earlier oto-core only returns `pages_fetched`, an honest fallback at 1 credit
# per page — which undercounts a Maps census by about two thirds, a Maps page being
# billed 3.
_MULTI_REQUEST = ("census_maps", "reviews_all")


def _as_count(value, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return default
    return int(value)


def credits_consumed(method: str, result) -> int:
    """The credits SERPER deducted for this tool call. PUBLIC because
    `web_read` is the backend's second serper mouth (step ② of its escalation) and must
    debit the same thing: a copied cost rule is a rule that diverges.

    The unit is the SERPER credit, as upstream declares it — no conversion to
    a billing unit is done here, and none should be: a rate is a
    commercial decision, it is not written into a connector.

    Read from the RESPONSE, never deduced from a rule coded here: Serper bills a Maps
    page of 100 results or a hard scrape more than one credit, and says so in its
    `credits` field. Falls back to 1 when upstream does not say: a successful response
    costs at least one credit."""
    if not isinstance(result, dict):
        return 1
    if method in _MULTI_REQUEST:
        raw = result.get("credits_used")
        if raw is None:
            raw = result.get("pages_fetched")
        return _as_count(raw, default=1)
    return _as_count(result.get("credits"), default=1)

# ONE client instance per key, for the whole process (oto#115). The oto-core client
# carries its rate limiter (a minimum interval between two requests) in the INSTANCE:
# rebuilt on every tool call, its counter restarted from zero and the declared limit
# never had any effect — the refusal came from the provider, in the middle of a job.
# Cache key = (factory, API key): the limit is that of a key, whether served by a
# `serper_*` tool or step ② of `web_read`; the factory is part of it so that a
# replaced class (test bench, reload) never serves an instance of the old one.
# The number of entries is bounded by that of distinct serper keys (platform + keys
# set by accounts): no eviction.
_CLIENTS: dict[tuple, object] = {}
_CLIENTS_VERROU = threading.Lock()


def client_for(key: str):
    """The Serper client for this key, the SAME from one call to the next. PUBLIC because
    `web_read` is the backend's second serper mouth and must share the limiter:
    two instances for one key are two counters that ignore each other."""
    from oto.tools.serper import SerperClient
    cle = (SerperClient, key)
    with _CLIENTS_VERROU:
        client = _CLIENTS.get(cle)
        if client is None:
            client = _CLIENTS[cle] = SerperClient(api_key=key)
        return client


# Serper returns `Serper <method> <status>: <msg>` (bare RuntimeError). Two classes
# of failure are invalid INPUTS, not backend bugs — we convert them into a
# HANDLED McpError (actionable message for the agent + not reported to Sentry, the
# taxonomy drops input McpErrors):
#  - **400** (generic, in `_run`): invalid request/URL — missing place param
#    (`Missing fid/cid/placeId`), non-scrapable URL (`Content-Type application/json`)…;
#  - **404 and 5xx** from scrape (in `serper_scrape`): the URL leads nowhere (dead
#    page) or the page blocked the bot. The 404 was the backend's #1 source of Sentry
#    noise — 37 events in 5 weeks for "the URL the agent found is dead",
#    which is an invalid input, not an outage.
# 401/403/429 (key/rate) stay propagated: real config problems. The EMPTY
# account (400 "Not enough credits", or 402) is translated separately, see `a_sec`.
_SERPER_STATUS = re.compile(r"Serper \w+ (\d{3}):")

# ⚠️ Serper says "empty account" with a **400** "Not enough credits", not a 402
# (measured on signals #1045, #1046, #1066): read like the other 400s, it returned
# `invalid_input` — "fix your call", on a correct call and an empty account — and the
# connector card stayed green. THIS signature alone is an empty balance; any
# other 400 stays an invalid input. A possible Serper 402 is read the same way: its
# client raises a BARE `RuntimeError`, without `.status_code`, which the taxonomy did not see.
_A_SEC = re.compile(r"Serper \w+ (?:400:.*not enough credits|402:)", re.I | re.S)

#: The refusal served to the agent when the Serper account is empty.
MSG_A_SEC = ("Serper: the account behind the served key is out of credits (\"Not enough credits\"). "
             "The call was correct: do not fix it and do not retry it.")


class SerperASec(RuntimeError):
    """Serper account out of credits. Carries `status_code = 402` ON PURPOSE: the taxonomy
    (`error_taxonomy`, step 0 → `quota_exhausted` + marking of the served key) and the
    probe (`connectors.verify.classer` → `no_quota`) then read it like any 402 —
    same refusal, same marking, no parallel path to maintain."""
    status_code = 402


# Serper answers a refused key with **403** `Unauthorized`, never a 401 — measured on
# signal #654 (`Serper search 403: Unauthorized` on every call of an org, three days) and
# reproduced by the connection probe. THIS signature alone is a dead key; any other 403
# is left unmarked (`error_taxonomy`: a 403 alone does not paint a key red).
_CLE_REFUSEE = re.compile(r"Serper \w+ 403:\s*Unauthorized", re.I)


class SerperCleRefusee(RuntimeError):
    """Serper refused the KEY (403 `Unauthorized`). Same message, still a RuntimeError
    (the callers that let a key problem propagate keep doing so); it carries the
    status and declares `credential_rejected`, which marks the served key red
    (`error_taxonomy.credential_rejected_in_chain`)."""
    status_code = 403
    credential_rejected = True


def cle_refusee(erreur: BaseException) -> "SerperCleRefusee | None":
    """`SerperCleRefusee` if `erreur` is Serper's refusal of the key, otherwise None."""
    return SerperCleRefusee(str(erreur)) if _CLE_REFUSEE.search(str(erreur)) else None


def a_sec(erreur: BaseException) -> "SerperASec | None":
    """`SerperASec` if `erreur` is Serper's "Not enough credits" refusal, otherwise
    None. PUBLIC: `web_read` (step ②) is the backend's second serper mouth."""
    return SerperASec(str(erreur)) if _A_SEC.search(str(erreur)) else None


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: probe contract, unused here)
    """"Test the connection" probe: a web search with ONE result.

    Serper exposes neither `/me` nor a credits endpoint: the only way to know
    that a key authenticates is to make the call the tools make. So we take
    the SAME endpoint as `serper_search` (`/search`), at the smallest size —
    same pattern as `tavily`/`firecrawl`, and a cost of at most one credit.

    Without this probe, `connectors.verify` refused "no connection test for
    serper" and a revoked key was only discovered by burning a real call in
    a run (signal #654: `Serper search 403: Unauthorized` on all of an org's
    calls, for three days, with no possible preflight).
    """
    from oto.tools.serper import SerperClient
    # Outside the `client_for` cache on purpose: the probed key is only a CANDIDATE.
    try:
        SerperClient(api_key=fields["key"]).search("oto", num=1)
    except RuntimeError as e:
        sec = a_sec(e)
        if sec is not None:     # `no_quota` verdict, not `unknown`
            raise sec from e
        raise


def register(mcp: FastMCP) -> None:
    # Import at register time to fail fast if the package is not installed.
    from oto.tools.serper import SerperClient

    connector_verify.register("serper", _verify)

    def _client() -> tuple[SerperClient, bool]:
        key, is_platform = access.resolve_api_key("serper")
        return client_for(key), is_platform

    def _refus_local(url: str) -> "str | None":
        """The reason this domain is NEVER scrapable, or `None`.

        ⚠️ Queried BEFORE the call, on purpose. The oto-core client knows the table and
        raises on it — but it raises a BARE `RuntimeError`, which the backend taxonomy
        cannot tell apart from a bug: it classifies it `internal` and serves "Erreur
        interne du serveur." **without echoing the message** (anti-leak). The model thus receives
        "internal error" where it should read "look for another source" — and
        it retries, or stops, instead of working around it (oto-backend#473).

        ⚠️ Measured on 2026-09-05: `classify(RuntimeError("… Facebook exige une session
        …"))` does return `Erreur interne du serveur.`. **The call log, though,
        shows the real message** — it records `str(exc)`, not what is served. Trusting it
        would lead to concluding the defect is fixed when it is not; that is
        the trap of this batch.

        Why in front rather than behind: classifying AFTER the fact would require reading the
        TEXT of the exception, and a classification built on words changes meaning at the
        first upstream reformatting. Here we ask the same question as the client, of the
        same table, before it raises.

        Best-effort on a private attribute of oto-core: if it disappears, we fall back
        exactly to the behaviour from before this batch — never worse. A
        version-skew test bench goes red in that case, so the degradation shows."""
        try:
            from oto.tools.serper import SerperClient
            return SerperClient._refuses_scraping(url)
        # IMPROVEMENT probe: its absence leaves the refusal opaque as before this batch,
        # it makes nothing worse. Logging on every scraped URL would drown the log for
        # a degradation that already has its signal — the version-skew bench
        # (`test_serper_refus_local_473`).
        except Exception:  # noqa: SILENT — best-effort probe, degradation covered by a test bench
            return None

    def _run(method: str, **kwargs) -> dict:
        """Resolves the key, calls the client method, counts platform usage.
        A Serper 400 (invalid input) → handled McpError (actionable, outside Sentry)."""
        client, is_platform = _client()
        try:
            result = getattr(client, method)(**kwargs)
        except RuntimeError as e:
            sec = a_sec(e)
            if sec is not None:
                # The 402 carried by the CAUSE does the job: `quota_exhausted` and
                # marking of the served key (`error_taxonomy`, step 0).
                raise McpError(ErrorData(code=INVALID_REQUEST, message=MSG_A_SEC)) from sec
            m = _SERPER_STATUS.search(str(e))
            if m and int(m.group(1)) == 400:
                raise McpError(ErrorData(code=INVALID_REQUEST, message=str(e))) from None
            refusee = cle_refusee(e)
            if refusee is not None:
                raise refusee from e
            raise
        # Same separation as theirstack/aiark: METERING is unconditional
        # (`tool_calls.key_mode` says separately under which key the call was made, and
        # billing only reads the partner's key); oto's internal QUOTA only counts
        # our key. Both in the number of Serper credits deducted, not 1 per call:
        # a Maps census cost up to 54 credits for a single "call".
        credits = credits_consumed(method, result)
        session_org.note_call_trace(quantity=credits)
        if is_platform:
            access.record_platform_usage("serper", credits)
        return result

    def _project(result: dict, items: str, full: bool, fields) -> dict:
        """Applies the projection to a results page. `full=True` returns the payload
        UNCHANGED (the escape hatch for whoever wants the knowledge graph); otherwise we remove
        what a sweep does not read, `fields` remaining an additional narrowing."""
        if full and not fields:
            return result
        return output_projection.project(
            result,
            drop=() if full else _SEARCH_DROP,
            items_path=items,
            item_drop=() if full else _RESULT_DROP,
            fields=fields)

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_REQUEST, message=msg))

    def _delai_lecture(timeout_s: Optional[int]) -> float:
        """Budget for OUR direct read: the one the caller gave itself
        if it tightened it, otherwise ours. An agent that asked for 3 s does not want
        to wait 20 more because the provider refused."""
        if timeout_s is None:
            return mail_obfuscation.LECTURE_DELAI_S
        return max(1, min(int(timeout_s), mail_obfuscation.LECTURE_DELAI_S))

    def _delai_scrape(timeout_s: Optional[int]) -> int:
        """The read timeout the scraper actually waited: the client's default
        (oto-core `_SCRAPE_TIMEOUT`), or `timeout_s` clamped to its bounds (1 to 60)."""
        from oto.tools.serper.client import _SCRAPE_TIMEOUT
        if timeout_s is None:
            return _SCRAPE_TIMEOUT[1]
        return max(1, min(int(timeout_s), 60))

    # Vertical → (client method, items path, params accepted on top of the base).
    # The common base is `query` + `num` + `page` + `country` + `language`: it is this
    # overlap that justifies the merge (ADR 0047 §Amendment — the criterion is
    # param homogeneity, not the count).
    _KINDS = {
        "web":      ("search",          "organic"),
        "news":     ("search_news",     "news"),
        "images":   ("search_images",   "images"),
        "videos":   ("search_videos",   "videos"),
        "places":   ("search_places",   "places"),
        "shopping": ("search_shopping", "shopping"),
        "scholar":  ("search_scholar",  "organic"),
        "patents":  ("search_patents",  "organic"),
    }
    _KIND_LIST = ", ".join(sorted(_KINDS) + ["autocomplete"])

    @mcp.tool(annotations=LECTURE)
    def serper_search(
        query: str,
        kind: Literal["web", "news", "images", "videos", "places", "shopping",
                      "scholar", "patents", "autocomplete"] = "web",
        num: int = 10,
        page: int = 1,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
        tbs: Optional[str] = None,
        location: Optional[str] = None,
        site_filter: Optional[str] = None,
        autocorrect: Optional[bool] = None,
        full: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Google search via Serper — one vertical per `kind`.

        `kind`:
        - **"web"** (default): organic results. Accepts `site_filter`
          (e.g. "linkedin.com/in"), `autocorrect`, `location`, `tbs`.
        - **"news"**: Google News — useful to monitor a target's signals
          (press releases, hires, fundraises). Accepts `tbs`.
        - **"images"** / **"videos"**: return an `images` / `videos` array
          (title, link, source, dimensions or duration). Accept `tbs`.
        - **"places"**: Google Local — the businesses for a query. Excellent
          for local B2B prospecting: title, address, phone, website, rating,
          review count and **`cid`** (to pass to `serper_reviews`). Accepts `location`.
        - **"shopping"**: title, price, merchant, rating, delivery. Accepts `location`.
        - **"scholar"**: academic publications (title, journal, year, citations, pdf).
        - **"patents"**: patents (title, inventor, applicant, number, dates).
        - **"autocomplete"**: Google suggestions for `query` — to widen a
          lexical field or find keyword ideas. Ignores pagination.

        Under a project with `excluded_url_prefixes`, the matching results are
        dropped and counted.

        Args:
            query: the query.
            kind: the vertical (default "web"): web | news | images | videos |
                places | shopping | scholar | patents | autocomplete.
            num: number of results (max 100). Ignored by "autocomplete".
            page: results page (1-based). Ignored by "autocomplete".
            country: country code (default "fr").
            language: language code (default "fr").
            tbs: Google time filter — "qdr:d" (24 h), "qdr:w" (7 d), "qdr:m"…
                Accepted by web / news / images / videos.
            location: geographic bias (e.g. "Paris, France"). Accepted by
                web / places / shopping.
            site_filter: kind="web" — restrict to a domain (e.g. "linkedin.com/in").
            autocorrect: kind="web" — toggles Google spelling correction.
            full: returns the ENTIRE Google response. By default (recommended) the
                return is narrowed to what a sweep reads — title, link, snippet — and
                drops knowledge graph, people-also-ask, related searches
                and per-result sitelinks (~a third of the payload, never read). Only pass
                `full=True` if you specifically want one of these sections.
                Accepted by web / news.
            fields: keep ONLY these keys on each result (e.g. ["title","link",
                "snippet"]). The envelope (credits, pagination) is kept in all
                cases. Accepted by web / news.
        """
        if kind == "autocomplete":
            return _run("autocomplete", query=query, country=country, language=language)

        entry = _KINDS.get(kind)
        if entry is None:
            raise _bad(f"Invalid `kind`: {kind!r} (expected: {_KIND_LIST}).")
        method, items = entry

        args = {"query": query, "num": num, "page": page,
                "country": country, "language": language}
        if kind in ("web", "news", "images", "videos"):
            args["tbs"] = tbs
        if kind in ("web", "places", "shopping"):
            args["location"] = location
        if kind == "web":
            args["site_filter"] = site_filter
            args["autocorrect"] = autocorrect

        # Project perimeter (#605) BEFORE the projection: `fields=` can remove `link`,
        # and a profile without its link would slip through.
        result = url_perimeter.filter_results(_run(method, **args),
                                              url_perimeter.perimeter_of_call())
        return _project(result, items, full, fields) if kind in ("web", "news") else result


    @mcp.tool(meta={"census_via": "serper_maps_census"}, annotations=LECTURE)
    def serper_maps_sample(
        query: Optional[str] = None,
        ll: Optional[str] = None,
        place_id: Optional[str] = None,
        cid: Optional[str] = None,
        num: int = 10,
        page: int = 1,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Google Maps — a SAMPLE of places, geographically anchored.

        ⚠️ The name says what this tool does: a sample, not an inventory. It
        caps at ~20 results per call and biases toward `ll` → it **silently
        undercounts** (20 found where 60 exist, without raising an error or
        announcing a total). For a count or an EXHAUSTIVE list of a type of
        business over an area ("how many X in Y"), use **`serper_maps_census`**,
        which tiles the area, paginates each anchor and deduplicates server-side.
        Rule: exact total → census; a few top hits → this tool.

        Args:
            query: the query (e.g. "coffee shops").
            ll: lat/long anchor + zoom "@lat,lng,zoom" (e.g. "@45.76,4.83,12z").
            place_id: Google id of a place, to look up directly.
            cid: Google customer id of a place.
            num: number of results (max 100).
            page: results page (1-based).
            country: country code (default "fr").
            language: language code (default "fr").
        """
        return _run(
            "search_maps", query=query, ll=ll, place_id=place_id, cid=cid,
            num=num, page=page, country=country, language=language,
        )

    @mcp.tool(meta={"technique": "local-census"}, annotations=LECTURE)
    def serper_maps_census(
        query: str,
        center: Optional[str] = None,
        radius_km: float = 5.0,
        grid: int = 3,
        zoom: int = 14,
        ll_anchors: Optional[list[str]] = None,
        max_pages: int = 3,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """EXHAUSTIVE census of a type of business over an area (Google Maps).

        Use this — NOT `serper_maps_sample` — whenever you need a **count or an
        exhaustive list** of a type of business over an area. A Maps sample
        caps at ~20 results and biases toward its anchor point: it **silently
        undercounts**. This tool fixes both server-side — it **tiles** the area
        into a grid of geo anchors, **paginates** each one and **deduplicates** by place id
        → complete result.

        Provide either `center` "lat,lng" (+ radius_km, grid), or `ll_anchors`.
        Cost: ~grid² × max_pages Serper requests (throttled), and a Maps page is
        billed **3 credits**, not 1 — a default census (grid=3, max_pages=3)
        therefore costs about 81 credits. That is the price of exhaustiveness; start
        modest and tighten the grid if needed.

        Returns {query, count, places[], anchors_used, pages_fetched, credits_used}.
        `count` = deduplicated total — to be preferred over any count from a sample alone.
        `credits_used` = what Serper ACTUALLY deducted across all the pages,
        the exact cost figure of this call (`pages_fetched` counts requests,
        not spend).

        Args:
            query: What is being enumerated (e.g. "self-service laundry").
            center: Area center "lat,lng" (e.g. "48.8566,2.3522"). Required unless ll_anchors.
            radius_km: Half-width of the square area around the center (default 5).
            grid: Tiling density grid×grid; finer = more coverage and more calls (default 3 → 9 anchors).
            zoom: Maps zoom level per anchor (default 14).
            ll_anchors: Explicit "@lat,lng,zoomz" anchors, take precedence over center/radius/grid.
            max_pages: Max pages paginated per anchor (default 3).
            country: Country code (default "fr").
            language: Language code (default "fr").
        """
        return _run(
            "census_maps", query=query, center=center, radius_km=radius_km,
            grid=grid, zoom=zoom, ll_anchors=ll_anchors, max_pages=max_pages,
            country=country, language=language,
        )

    @mcp.tool(meta={"technique": "reviews-census"}, annotations=LECTURE)
    def serper_reviews(
        op: Literal["all", "page"] = "all",
        cid: Optional[str] = None,
        fid: Optional[str] = None,
        place_id: Optional[str] = None,
        query: Optional[str] = None,
        sort_by: Optional[str] = None,
        topic_id: Optional[str] = None,
        max_reviews: int = 200,
        next_page_token: Optional[str] = None,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Google reviews of a place.

        ⚠️ **The default returns ALL the reviews, and that is intended.** A single page
        (~10 reviews, sorted `mostRelevant`) **silently under-represents** a place
        that can have thousands: a sentiment analysis done on it is
        biased without anything signalling it. The default path is therefore the
        complete path; the sample must be requested explicitly.

        `op`:
        - **"all"** (default): follows the `nextPageToken` cursor server-side until
          exhausted, or until the `max_reviews` cap (bounds the cost;
          `truncated=True` signals the cut). Returns {count, reviews[],
          pages_fetched, credits_used, truncated} — `credits_used` = what Serper
          actually deducted across all the pages, the exact cost of the call.
          This is what you need for global sentiment, recurring themes, a
          reputation.
        - **"page"**: ONE page (~10 reviews) — quick sample, or manual pagination
          via `next_page_token`. Conclude nothing global from it.

        Identify the place by `cid` / `fid` / `place_id` (from a
        `serper_search(kind="places")` or a `serper_maps_sample`) or by free `query`.

        Args:
            op: "all" (default) | "page".
            cid: Google customer id of the place.
            fid: Google feature id of the place.
            place_id: Google place id.
            query: free-text place lookup (alternative to ids).
            sort_by: 'mostRelevant' | 'newest' | 'highestRating' | 'lowestRating'.
            topic_id: filters the reviews by topic.
            max_reviews: op="all" — cap on reviews fetched (default 200).
            next_page_token: op="page" — cursor from a previous response.
            country: country code (default "fr").
            language: language code (default "fr").
        """
        if op == "all":
            return _run(
                "reviews_all", cid=cid, fid=fid, place_id=place_id, query=query,
                sort_by=sort_by, topic_id=topic_id, max_reviews=max_reviews,
                country=country, language=language,
            )
        if op == "page":
            return _run(
                "search_reviews", cid=cid, fid=fid, place_id=place_id, query=query,
                sort_by=sort_by, topic_id=topic_id, next_page_token=next_page_token,
                country=country, language=language,
            )
        raise _bad(f"Invalid `op`: {op!r} (expected: all | page).")

    @mcp.tool(annotations=LECTURE)
    def serper_lens(
        url: str,
        country: Optional[str] = "fr",
        language: Optional[str] = "fr",
    ) -> dict:
        """Google Lens via Serper — reverse search from an image.

        Args:
            url: public URL of the image to analyse — refused under the project's
                `excluded_url_prefixes`, which also drop the results.
            country: country code (default "fr").
            language: language code (default "fr").
        """
        per = url_perimeter.perimeter_of_call()
        url_perimeter.refuse_if_excluded(url, per)
        return url_perimeter.filter_results(
            _run("search_lens", url=url, country=country, language=language), per)

    @mcp.tool(annotations=LECTURE)
    def serper_scrape(
        url: str,
        format: Literal["markdown", "text", "both", "html"] = "markdown",
        timeout_s: Optional[int] = None,
    ) -> dict:
        """Fetches a web page via Serper's scraper.

        ⚠️ **JS rendering is not guaranteed.** A client-side rendered site returns
        HTTP 200 and an almost empty body, without any error: a very
        short body therefore does NOT prove the page is empty. Check its length before
        drawing a fact about the company from it.

        ⚠️ **A call waits at most 15 seconds**, then returns a refusal that says so —
        even on a domain that does not exist. A timeout is a NORMAL failure,
        not an outage. **Start from a URL you have observed**, and do not retry
        the same one.

        ⚠️ **Obfuscated addresses do not survive rendering** (`mailto:` in
        HTML entities, base64, Cloudflare protection): an address READABLE in
        the HTML is INVISIBLE in the markdown. A `motifs_obfuscation` without
        addresses means "there is a contact here, not decoded": retry with
        format="html".

        Returns the content in ONE representation (markdown by default) + JSON-LD +
        metadata. More robust than a raw fetch against rudimentary anti-bot measures.
        Images included as base64 are removed from the content (a trace gives their type
        and size, `images_base64_retirees` counts them); `format="html"` keeps them.

        On the measured calls, half of the timeouts were on addresses
        made up from a company name: it is not the slowness that
        costs, it is what it carries away — while you wait, your own
        context gets re-billed. When the served page shows no address,
        the tool re-reads the HTML itself and returns `adresses_obfusquees` — it appends
        the same thing at the bottom of the content; `sonde_obfuscation` says why the
        re-read concluded nothing.

        Args:
            url: URL of the page to fetch — refused if it falls under the project's
                `excluded_url_prefixes`.
            format: "markdown" (default, the LLM-readable representation) |
                "text" (raw) | "both" (only if you really need to
                compare) | "html" (the page's RAW HTML, through our own
                request, without the scraper and without credit — to check
                yourself what a rendering may have lost; capped, the total is
                given in `html_caracteres`).
            timeout_s: seconds to wait, 1 to 60 (default 15). Tighten it when you
                chain many dubious pages; an out-of-bounds value is
                clamped inside, never refused.
        """
        # The perimeter refusal speaks FIRST (#632): before the validation of
        # `format`, before the upstream client's rule on closed hosts.
        per = url_perimeter.perimeter_of_call()
        url_perimeter.refuse_if_excluded(url, per)
        # oto-backend#473: is the domain one we NEVER scrape? We say so HERE, in plain
        # and actionable terms, rather than letting the client raise a bare
        # `RuntimeError` that the taxonomy will render as "Erreur interne du serveur.".
        # Permanent regime, not an incident: measured again on 2026-09-04 on
        # facebook.com URLs, in runs that could only give up.
        if (pourquoi := _refus_local(url)):
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=(f"{url} cannot be read by this scraper: {pourquoi} "
                         "This refusal is FINAL for this domain — do not retry the "
                         "same address, use another source.")))
        if format not in ("markdown", "text", "both", "html"):
            raise _bad(
                f"Invalid `format`: {format!r} (markdown | text | both | html).")
        if format == "html":
            # The hosted scraper returns NO HTML field: asking for it
            # through it would have yielded nothing. So we read the page ourselves —
            # but without reopening the door the client shuts outright on
            # sources closed to extraction (login wall).
            ferme = SerperClient._refuses_scraping(url)
            if ferme:
                raise _bad(f"No raw HTML for {url}: {ferme}")
            return mail_obfuscation.html_brut(url, per, _delai_lecture(timeout_s))
        try:
            res = _run("scrape_page", url=url, include_markdown=format != "text",
                       timeout_s=timeout_s)
            # Serper used to return `text` AND `markdown`: two representations of the SAME
            # content (measured, 97% shared words), for 37% of the payload in pure
            # duplication. We only serve one — removing a duplicate loses nothing. The
            # JSON-LD and the metadata STAY: they are not representations
            # of the content but structured data (date, author) that could not
            # be reconstructed, so removing them would lose something.
            if format == "markdown" and res.get("markdown"):
                res.pop("text", None)
            # oto#246: an image included as base64 cannot be read, it is paid for on every
            # turn that re-reads the page — up to 91% of a measured home page.
            images_base64.alleger(res)
            # oto#208: the soft hyphen shows up in the middle of words, proper nouns
            # included. Removed BEFORE the address completion, which re-reads the text.
            cesures.retirer_des_representations(res)
            mail_obfuscation.completer(res, url, per)
            return res
        except requests.Timeout:
            # The site did not answer within the delay (2026-09-14). A NORMAL failure, not an
            # outage: oto-core documents it that way (`SerperClient.scrape_page`), and #662
            # measured that what a wait carries away costs more than the page — hence no
            # fallback here. It used to exit as an error with a trace, go up to Sentry, and the agent
            # read "retry in a moment": 32 timeouts in 40 minutes measured that
            # day. As a refusal, it fits on one line and says not to retry.
            raise McpError(ErrorData(
                code=INVALID_REQUEST,
                message=(f"Cannot scrape this URL ({url}): the site did not "
                         f"answer within {_delai_scrape(timeout_s)} s. A timeout is a "
                         "normal failure, not an outage: do not retry this address."),
            )) from None
        except RuntimeError as e:
            m = _SERPER_STATUS.search(str(e))
            code = int(m.group(1)) if m else None
            if code == 404 or (code is not None and 500 <= code < 600):
                # The provider refused — before handing back, we re-read the
                # page OURSELVES with a browser UA. On the 09-03 tier,
                # three refused sites out of four (two Wix, one
                # WordPress.com) answered normally to that request
                # (#681). The fallback SAYS its path, it does not disguise itself as a
                # scrape. Deliberately not on a TIMEOUT: that one has
                # already consumed the caller's budget, and #662 measured that
                # what a wait carries away costs more than the page.
                recuperee, pourquoi = mail_obfuscation.repli(
                    url, per, _delai_lecture(timeout_s))
                if recuperee:
                    return recuperee
                detail = ("this page does not exist (or no longer)" if code == 404 else
                          "the page blocked the bot or could not be fetched")
                raise McpError(ErrorData(
                    code=INVALID_REQUEST,
                    message=(f"Cannot scrape this URL ({url}): {detail}. "
                             f"Our own direct read failed too: "
                             f"{pourquoi}. Try another source or serper_search."),
                )) from None
            raise
