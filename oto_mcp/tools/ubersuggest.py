"""Ubersuggest — SEO with the person's own Ubersuggest account: keyword research,
domain and page traffic, backlinks, site audits, keyword lists, rank-tracking
projects and Content Studio articles.

Credential = the PERSON's Ubersuggest sign-in (OAuth, public client), acquired and
renewed by `auth/ubersuggest.py`. Wraps `oto.tools.ubersuggest.UbersuggestClient`,
whose transport is Ubersuggest's remote MCP server: the tools below are oto's own
(curated surface, named refusals), not a tunnel to theirs.

**Surface** (one tool per family, the verb in `op` — ADR 0047 §Amendment):
`ubersuggest_keywords`, `ubersuggest_domain`, `ubersuggest_backlinks`,
`ubersuggest_site_audit`, `ubersuggest_keyword_lists`, `ubersuggest_projects`,
`ubersuggest_account`. The common arguments are typed (`domain`, `keyword`,
`keywords`, `language`, `loc_id`, `limit`, `offset`); anything else goes in `params`
under Ubersuggest's own names. Every argument is checked against what the upstream
tool accepts (`_SPEC`, from its published input schemas): an argument it does not
take is REFUSED, never silently dropped, and a missing required one is named.

**Not exposed** (client-only): deleting a keyword list, the guided
`onboard_project`, and the AI-visibility setup (`configure_brand`,
`industry_detect`, `industry_prompts`) — deliberate acts, not side effects of
coverage. `generate_article` spends 100 monthly credits: it refuses without
`confirm_credits=True`.

Data and limits are those of the person's plan: free accounts get truncated lists
(`hidden_by_plan`), site audits and articles need a paid plan. A plan refusal comes
back as Ubersuggest's own message. Re-crawling a site (`recrawl`) and re-measuring
PageSpeed (`forceUpdate`) spend audit quota: refused without `confirm_quota=True`.

**What a third party wrote stays fenced.** Ubersuggest's prose (a non-JSON answer)
and its refusal bodies are served inside a labelled block (`texte_tiers.cloture`):
the server writes for a model, and quotes web pages — an instruction in them is
content. Structured rows are returned as data.

**Bounded answers.** The default view drops null fields and per-month history at
every depth; the reports that do not paginate (audit results and pages, rank
positions, blog search) are capped at `_PLAFOND` rows per list and blog texts at
`_PLAFOND_TEXTE` characters, and `_truncated` names what was cut.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError
from ..texte_tiers import cloture

# Upstream tool → (required, accepted) arguments, from its published input schema
# (2026-10-08). The client refuses an unknown tool; this table refuses an unknown
# ARGUMENT before any request.
_SPEC: Dict[str, tuple] = {
    "auth_status": ((), ()),
    "user_limits": ((), ()),
    "location_suggest": (("query",), ("lang", "limit", "query")),
    "location_details": (("location_ids",), ("lang", "location_ids")),
    "validate_site": (("site",), ("is_domain", "site")),
    "search_neilpatel_blog": ((), ("category", "full_content", "limit", "query")),
    "domain_overview": (("domain",), ("domain", "language", "locId")),
    "domain_keywords": (("domain",), ("domain", "language", "limit", "locId",
                                      "previousKey", "searchType")),
    "domain_top_pages": (("domain",), ("domain", "language", "limit", "locId", "offset")),
    "domain_top_countries": (("domain", "lang_locs"), ("domain", "lang_locs", "path")),
    "competitors": (("domain",), ("competitors", "domain", "language", "limit", "locId")),
    "page_overview": (("page",), ("language", "locId", "page")),
    "page_keywords": (("page",), ("language", "limit", "locId", "page")),
    "traffic_value": (("domain",), ("domain", "project_id")),
    "keyword_overview": (("keyword",), ("keyword", "language", "locId")),
    "keyword_suggestions": (("keywords",), ("keywords", "language", "locId")),
    "keyword_metrics": (("keyword", "language", "metric"), ("keyword", "language",
                                                            "locId", "metric")),
    "serp_analysis": (("keyword",), ("keyword", "language", "limit", "locId")),
    "match_keywords": (("keywords",), ("domain", "keywords", "language", "limit", "locId",
                                       "offset", "sortby")),
    "google_suggestions": (("keywords",), ("country", "keywords", "language")),
    "estimate_serp_clicks": (("serps",), ("serps",)),
    "content_ideas": (("keywords",), ("filters", "keywords", "language", "limit", "locId",
                                      "offset", "sortby")),
    "keyword_lists": ((), ()),
    "keyword_list": (("list_id",), ("limit", "list_id", "offset")),
    "create_keyword_list": (("name",), ("keywords", "name")),
    "add_keywords_to_list": (("list_id", "keywords"), ("keywords", "list_id")),
    "remove_keywords_from_list": (("list_id", "keywords"), ("keywords", "list_id")),
    "rename_keyword_list": (("list_id", "name"), ("list_id", "name")),
    "backlinks_overview": (("domain",), ("domain",)),
    "backlinks": (("domain",), ("domain", "limit", "mode", "offset", "one_per_domain",
                                "order_by")),
    "anchor_texts": (("domain",), ("domain", "limit", "mode", "offset")),
    "linking_domains": (("domain",), ("begin_date", "domain", "end_date", "filter_by",
                                      "limit", "mode", "offset")),
    "backlink_opportunity": (("positive_targets",), ("limit", "negative_targets", "offset",
                                                     "positive_targets")),
    "page_shares": (("page_urls",), ("language", "locId", "mode", "page_urls")),
    "site_audit": (("domain",), ("crawlMaxPages", "domain", "path", "recrawl")),
    "site_audit_status": (("domain",), ("crawlMaxPages", "domain", "path")),
    "site_audit_results": (("domain", "issue"), ("domain", "issue", "path")),
    "site_audit_pages": (("domain",), ("domain",)),
    "pagespeed_audit": (("domain",), ("devices", "domain", "forceUpdate")),
    "list_projects": ((), ()),
    "get_project": (("project_id",), ("project_id",)),
    "project_position_info": (("project_id", "startDate", "endDate"),
                              ("device", "endDate", "language", "locId", "project_id",
                               "startDate")),
    "seo_opportunities": (("project_id",), ("project_id",)),
    "create_project": (("domain", "locations"), ("business_summary", "competitors",
                                                  "domain", "keywords", "locations",
                                                  "project_type", "title")),
    "add_project_keywords": (("project_id", "keywords"), ("keywords", "project_id")),
    "add_project_competitors": (("project_id", "competitors"),
                                ("competitors", "competitors_locations", "project_id")),
    "project_business_summary": ((), ("business_summary", "domain", "language",
                                      "project_id")),
    "brand_config": (("project_id",), ("project_id",)),
    "brand_visibility_overview": (("project_id",), ("end_date", "project_id", "provider",
                                                    "start_date")),
    "brand_prompts": (("project_id",), ("end_date", "project_id", "provider",
                                        "start_date")),
    "article_title_suggestions": (("project_id",), ("keyword", "language", "locId",
                                                    "project_id", "prompt",
                                                    "source_type")),
    "generate_article": (("project_id", "title", "content_idea"),
                         ("content_idea", "keyword", "language", "locId", "project_id",
                          "prompt", "source_type", "title")),
    "get_article": (("project_id", "article_id"), ("article_id", "project_id")),
}

_KEYWORDS_OPS = {"overview": "keyword_overview", "suggestions": "keyword_suggestions",
                 "match": "match_keywords", "google": "google_suggestions",
                 "serp": "serp_analysis", "metric": "keyword_metrics",
                 "content_ideas": "content_ideas", "estimate_clicks": "estimate_serp_clicks"}
_DOMAIN_OPS = {"overview": "domain_overview", "keywords": "domain_keywords",
               "top_pages": "domain_top_pages", "top_countries": "domain_top_countries",
               "competitors": "competitors", "traffic_value": "traffic_value",
               "page_overview": "page_overview", "page_keywords": "page_keywords"}
_BACKLINKS_OPS = {"overview": "backlinks_overview", "list": "backlinks",
                  "anchors": "anchor_texts", "linking_domains": "linking_domains",
                  "opportunity": "backlink_opportunity", "page_shares": "page_shares"}
_AUDIT_OPS = {"start": "site_audit", "status": "site_audit_status",
              "results": "site_audit_results", "pages": "site_audit_pages",
              "pagespeed": "pagespeed_audit"}
_LISTS_OPS = {"list": "keyword_lists", "get": "keyword_list",
              "create": "create_keyword_list", "add": "add_keywords_to_list",
              "remove": "remove_keywords_from_list", "rename": "rename_keyword_list"}
_PROJECTS_OPS = {"list": "list_projects", "get": "get_project",
                 "positions": "project_position_info",
                 "opportunities": "seo_opportunities", "create": "create_project",
                 "add_keywords": "add_project_keywords",
                 "add_competitors": "add_project_competitors",
                 "business_summary": "project_business_summary",
                 "brand_config": "brand_config",
                 "brand_overview": "brand_visibility_overview",
                 "brand_prompts": "brand_prompts",
                 "article_titles": "article_title_suggestions",
                 "generate_article": "generate_article", "get_article": "get_article"}
_ACCOUNT_OPS = {"status": "auth_status", "limits": "user_limits",
                "locations": "location_suggest", "location_details": "location_details",
                "validate_site": "validate_site", "blog": "search_neilpatel_blog"}

# Per-month history attached to every row of a list: the bulk of a list's size,
# rarely read in a list. Kept by `full=True`.
_HEAVY = frozenset({"monthly_searches", "monthlySearches", "trend", "trends"})

# Reports that return everything at once (no cursor): rows kept per list, characters
# kept per text field.
_PLAFOND = 100
_PLAFOND_TEXTE = 4000
_BORNES = frozenset({"site_audit_results", "site_audit_pages", "project_position_info",
                     "search_neilpatel_blog"})
# Upstream arguments that relaunch a costly job, and what they spend.
_RELANCES = {"recrawl": "a full re-crawl of the site (site audit quota)",
             "forceUpdate": "a new PageSpeed measurement (audit quota)"}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _arguments(upstream: str, typed: Dict[str, Any], params: Optional[dict]) -> dict:
    """Typed arguments (oto names) + `params` (Ubersuggest names) → the upstream call's
    arguments, checked against `_SPEC`. `loc_id` is `locId` upstream; `offset` is
    `previousKey` where the tool paginates by that name."""
    required, accepted = _SPEC[upstream]
    args: Dict[str, Any] = {}
    for name, value in typed.items():
        if value is None:
            continue
        if name == "loc_id":
            name = "locId"
        elif name == "offset" and "offset" not in accepted and "previousKey" in accepted:
            name = "previousKey"
        if name not in accepted:
            raise _bad(f"`{upstream}` does not take `{name}`. It accepts: "
                       f"{', '.join(accepted) or 'nothing'}.")
        args[name] = value
    for name, value in (params or {}).items():
        if name not in accepted:
            raise _bad(f"`{upstream}` does not take `{name}` (in `params`). It accepts: "
                       f"{', '.join(accepted) or 'nothing'}.")
        if name in args and args[name] != value:
            raise _bad(f"`{name}` is given twice, with two values.")
        args[name] = value
    missing = [r for r in required if args.get(r) in (None, "", [], {})]
    if missing:
        raise _bad(f"`{upstream}` requires {', '.join(f'`{m}`' for m in missing)}.")
    return args


def _slim(data: Any, full: bool) -> Any:
    """Default view: rows of a list without null fields nor per-month history, at
    EVERY depth (`{"result": {"keywords": [...]}}` is slimmed like a bare list).
    `full=True` returns Ubersuggest's answer as is."""
    if full:
        return data
    if isinstance(data, list):
        return [_slim(_slim_row(r), False) for r in data]
    if isinstance(data, dict):
        return {k: _slim(v, False) for k, v in data.items()}
    return data


def _borner(data: Any) -> Any:
    """Caps every list at `_PLAFOND` rows and every text at `_PLAFOND_TEXTE`
    characters, at any depth; `_truncated` (on a dict answer, or around a list one)
    names each cut with its full size."""
    coupes: Dict[str, int] = {}

    def walk(x: Any, chemin: str) -> Any:
        if isinstance(x, list):
            if len(x) > _PLAFOND:
                coupes[chemin or "."] = len(x)
            return [walk(v, f"{chemin}[]") for v in x[:_PLAFOND]]
        if isinstance(x, dict):
            return {k: walk(v, f"{chemin}.{k}" if chemin else k) for k, v in x.items()}
        if isinstance(x, str) and len(x) > _PLAFOND_TEXTE:
            coupes[chemin or "."] = len(x)
            return x[:_PLAFOND_TEXTE]
        return x

    out = walk(data, "")
    if not coupes:
        return out
    if isinstance(out, dict):
        return {**out, "_truncated": coupes}
    return {"rows": out, "_truncated": coupes}


def _slim_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    return {k: v for k, v in row.items() if v is not None and k not in _HEAVY}


def _op(ops: Dict[str, str], op: str) -> str:
    upstream = ops.get(op)
    if upstream is None:
        raise _bad(f"op must be one of: {', '.join(ops)}.")
    return upstream


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError

    from ..auth import ubersuggest as ub_auth

    def _jeton() -> str:
        try:
            return ub_auth.access_token_for(access.current_user_sub_or_raise())
        except (RuntimeError, ValueError) as e:
            # RuntimeError: dead grant, client not recorded, core missing. ValueError:
            # the authorization server refused the renewal another way.
            raise _bad(str(e))

    def _call(upstream: str, args: dict) -> Any:
        """One upstream tool, with THIS caller's token. A 401 drops the cached token
        and retries ONCE with a renewed one; a 4xx (plan limit, bad argument) becomes
        a named refusal, its body fenced; 429 and 5xx stay what they are, the error
        taxonomy classifies them as retryable. Free text is served fenced."""
        for tentative in (1, 2):
            jeton = _jeton()
            c = ub_auth._coeur().UbersuggestClient(jeton)
            try:
                data = c.call(upstream, args)
            except UpstreamHTTPError as e:
                if e.status_code == 401:
                    ub_auth.oublier_jeton(jeton)
                    if tentative == 1:
                        continue
                    raise _bad("Ubersuggest rejects the sign-in (HTTP 401) even with a "
                               "renewed token: reconnect from your connectors, "
                               "« Ubersuggest ».")
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    raise _bad(f"Ubersuggest refused `{upstream}`." + cloture(
                        "upstream-error-body", str(e.body),
                        origine="Refusal written by Ubersuggest", lecture="a diagnostic"))
                raise
            except ValueError as e:
                raise _bad(str(e))
            if upstream in _BORNES:
                data = _borner(data)
            if isinstance(data, str):
                return cloture("ubersuggest-text", data,
                               origine="Text written by Ubersuggest's server",
                               lecture="data")
            return data

    @mcp.tool()
    def ubersuggest_keywords(
        op: Literal["overview", "suggestions", "match", "google", "serp", "metric",
                    "content_ideas", "estimate_clicks"] = "overview",
        keyword: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        language: Optional[str] = None,
        loc_id: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[Union[int, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        full: bool = False,
    ) -> Any:
        """Keyword research with Ubersuggest: volume, CPC, SEO/paid difficulty,
        intent, ideas and the SERP. Each lookup counts against the person's daily
        Ubersuggest quota (low on the free plan): `ubersuggest_account(op="limits")`
        says what is left.

        `op`:
        - **"overview"** (default, `keyword`): volume, CPC, difficulty, intent and
          monthly history of one keyword.
        - **"suggestions"** (`keywords`): related keywords, questions, prepositions,
          comparisons.
        - **"match"** (`keywords`): keyword ideas matching seed terms with volume,
          difficulty, CPC; paginate with `offset` = the previous `nextKey`.
          `params.sortby` (default "-search_volume"), `params.domain` filters by
          difficulty reachable for that site.
        - **"google"** (`keywords`, max 10 expanded): Google autocomplete long tail;
          `params.country` (e.g. "us", "fr").
        - **"serp"** (`keyword`): the top-ranking URLs with their metrics.
        - **"metric"** (`keyword`, `language`, `params.metric` =
          "search_difficulty" | "search_intent"): recomputes one metric (~30 s;
          search_difficulty spends the monthly metrics quota).
        - **"content_ideas"** (`keywords`): top pages by shares, visits, backlinks;
          `params.sortby` (e.g. "-estVisits"), `params.filters`.
        - **"estimate_clicks"** (`params.serps` = [{searchVolume, position, type}]):
          a click calculator, it looks nothing up.

        Args:
            op: see above.
            keyword: one keyword (overview, serp, metric).
            keywords: seed keywords (suggestions, match, google, content_ideas).
            language: language code, e.g. "en", "fr" (default "en" upstream).
            loc_id: Google location id from `ubersuggest_account(op="locations")`
                (2840 US, 2250 France, 2826 UK…); omit for global data.
            limit: max rows (match, serp, content_ideas).
            offset: pagination cursor from the previous response (match, content_ideas),
                passed back as given — a number or a string.
            params: any other argument, under Ubersuggest's own name.
            full: True returns rows as is; the default drops null fields and the
                per-month history of each row of a list.
        """
        upstream = _op(_KEYWORDS_OPS, op)
        args = _arguments(upstream, {"keyword": keyword, "keywords": keywords,
                                     "language": language, "loc_id": loc_id,
                                     "limit": limit, "offset": offset}, params)
        return _slim(_call(upstream, args), full)

    @mcp.tool()
    def ubersuggest_domain(
        op: Literal["overview", "keywords", "top_pages", "top_countries", "competitors",
                    "traffic_value", "page_overview", "page_keywords"] = "overview",
        domain: Optional[str] = None,
        page: Optional[str] = None,
        language: Optional[str] = None,
        loc_id: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[Union[int, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        full: bool = False,
    ) -> Any:
        """A domain's or a page's organic search footprint, as Ubersuggest sees it.
        Each lookup counts against the person's daily
        Ubersuggest quota (low on the free plan): `ubersuggest_account(op="limits")`
        says what is left.

        `op`:
        - **"overview"** (default, `domain`): traffic, organic keyword count, domain
          authority, backlinks summary.
        - **"keywords"** (`domain`): keywords the domain ranks for, with position,
          volume, CPC, difficulty, traffic, URL; `params.searchType` = "organic"
          (default) | "paid"; paginate with `offset`.
        - **"top_pages"** (`domain`): pages by estimated traffic.
        - **"top_countries"** (`domain`, `params.lang_locs` = ["en:2840", "fr:2250"]):
          traffic split by country; `params.path` for one page.
        - **"competitors"** (`domain`): main organic competitors (async upstream: if
          `pendingData` is true, call again shortly); `params.competitors` to analyze
          given domains instead.
        - **"traffic_value"** (`domain`, `params.project_id`): USD value of the
          organic traffic of a tracked project's domain.
        - **"page_overview"** / **"page_keywords"** (`page` = a full URL): one page's
          traffic and ranking keywords.

        Args:
            op: see above.
            domain: e.g. "example.com".
            page: a full page URL (page_overview, page_keywords).
            language: language code (default "en" upstream).
            loc_id: Google location id from `ubersuggest_account(op="locations")`.
            limit: max rows.
            offset: pagination cursor from the previous response (`previousKey` for
                op="keywords"), passed back as given — a number or a string.
            params: any other argument, under Ubersuggest's own name.
            full: True returns rows as is; the default drops null fields and the
                per-month history of each row of a list.
        """
        upstream = _op(_DOMAIN_OPS, op)
        args = _arguments(upstream, {"domain": domain, "page": page, "language": language,
                                     "loc_id": loc_id, "limit": limit, "offset": offset},
                          params)
        return _slim(_call(upstream, args), full)

    @mcp.tool()
    def ubersuggest_backlinks(
        op: Literal["overview", "list", "anchors", "linking_domains", "opportunity",
                    "page_shares"] = "overview",
        domain: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[Union[int, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        full: bool = False,
    ) -> Any:
        """Backlinks of a domain or a page, and link-building opportunities.
        Each lookup counts against the person's daily
        Ubersuggest quota (low on the free plan): `ubersuggest_account(op="limits")`
        says what is left.

        `op`:
        - **"overview"** (default, `domain`): backlinks, referring domains, authority.
        - **"list"** (`domain`): individual backlinks; `params.mode` = "domain" |
          "url" | "host" | "page", `params.order_by`, `params.one_per_domain`.
        - **"anchors"** (`domain`): anchor text distribution (max 25 per page).
        - **"linking_domains"** (`domain`): referring domains recently gained or lost;
          `params.filter_by` = "new" (default) | "lost", `params.begin_date`/`end_date`.
        - **"opportunity"** (`params.positive_targets` = [{target, scope: "domain"}]
          for competitors, `params.negative_targets` for your own site): domains that
          link to them and not to you.
        - **"page_shares"** (`params.page_urls`): social shares plus backlink and
          traffic metrics for a batch of URLs.

        Pagination: pass back the previous response's `previousKey` as `offset`.

        Args:
            op: see above.
            domain: target domain, or a full URL with `params.mode="url"`.
            limit: max rows.
            offset: pagination cursor (`previousKey`), passed back as given.
            params: any other argument, under Ubersuggest's own name.
            full: True returns rows as is; the default drops null fields of each row.
        """
        upstream = _op(_BACKLINKS_OPS, op)
        args = _arguments(upstream, {"domain": domain, "limit": limit, "offset": offset},
                          params)
        return _slim(_call(upstream, args), full)

    @mcp.tool()
    def ubersuggest_site_audit(
        op: Literal["start", "status", "results", "pages", "pagespeed"] = "status",
        domain: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        confirm_quota: bool = False,
    ) -> Any:
        """Technical SEO audit of a site (paid Ubersuggest plan), and PageSpeed.

        Flow: **"start"** (`domain`; `params.crawlMaxPages`, `params.recrawl`,
        `params.path` for one page) launches the crawl → **"status"** (default)
        until `result.done` is true: it carries the health score and the issue
        counts per category → **"results"** (`params.issue` = an issue id from the
        status report, e.g. "seo_missing_h1") lists the affected URLs →
        **"pages"** lists crawled URLs with HTTP status.
        **"pagespeed"** (`domain`; `params.devices` = "DESKTOP,MOBILE",
        `params.forceUpdate`): Core Web Vitals and improvement opportunities (can take
        up to ~2 min).

        `params.recrawl` and `params.forceUpdate` relaunch a costly job (audit quota):
        refused unless `confirm_quota=True`, passed only after the person agreed.
        "results" and "pages" are capped at 100 rows (`_truncated` says what was cut).

        Args:
            op: start | status (default) | results | pages | pagespeed.
            domain: root domain, e.g. "example.com".
            params: any other argument, under Ubersuggest's own name.
            confirm_quota: required True with `recrawl` or `forceUpdate`.
        """
        upstream = _op(_AUDIT_OPS, op)
        relances = [k for k in _RELANCES if (params or {}).get(k)]
        if relances and not confirm_quota:
            raise _bad("This relaunches " + " and ".join(_RELANCES[k] for k in relances)
                       + ": ask the person, then call again with confirm_quota=True.")
        return _call(upstream, _arguments(upstream, {"domain": domain}, params))

    @mcp.tool()
    def ubersuggest_keyword_lists(
        op: Literal["list", "get", "create", "add", "remove", "rename"] = "list",
        list_id: Optional[str] = None,
        name: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        full: bool = False,
    ) -> Any:
        """The person's saved keyword lists in Ubersuggest.

        `op`: **"list"** (default) the lists; **"get"** (`list_id`) a list's keywords
        with their metrics; **"create"** (`name`, optional `keywords`); **"add"** /
        **"remove"** (`list_id`, `keywords`); **"rename"** (`list_id`, `name`).
        Deleting a list is not exposed. Plan caps apply: a free account reaches only
        its three newest lists (`hidden_by_plan` says what is hidden).

        Args:
            op: see above.
            list_id: the list's id, from op="list".
            name: list name (create, rename).
            keywords: keywords to add/remove (or to seed a new list).
            limit: max keywords (get).
            offset: pagination offset (get).
            full: True returns rows as is; the default drops null fields and the
                per-month history of each row of a list.
        """
        upstream = _op(_LISTS_OPS, op)
        args = _arguments(upstream, {"list_id": list_id, "name": name, "keywords": keywords,
                                     "limit": limit, "offset": offset}, None)
        return _slim(_call(upstream, args), full)

    @mcp.tool()
    def ubersuggest_projects(
        op: Literal["list", "get", "positions", "opportunities", "create",
                    "add_keywords", "add_competitors", "business_summary",
                    "brand_config", "brand_overview", "brand_prompts",
                    "article_titles", "generate_article", "get_article"] = "list",
        project_id: Optional[str] = None,
        domain: Optional[str] = None,
        keyword: Optional[str] = None,
        language: Optional[str] = None,
        loc_id: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        confirm_credits: bool = False,
    ) -> Any:
        """Rank-tracking projects, AI search visibility and Content Studio articles.

        Projects:
        - **"list"** (default), **"get"** (`project_id`).
        - **"positions"** (`project_id`, `params.startDate`, `params.endDate` as
          YYYY-MM-DD; `params.device`): rank tracking report of tracked keywords,
          capped at 100 rows per list (`_truncated` says what was cut).
        - **"opportunities"** (`project_id`): SEO opportunities found for the project.
        - **"create"** (`domain`, `params.locations`; `params.keywords` =
          {"phrase": [{"lang": "en", "loc_id": 2840}]}, `params.competitors` same
          shape, `params.title`). Writes to the account.
        - **"add_keywords"** (`project_id`, `params.keywords`), **"add_competitors"**
          (`project_id`, `params.competitors`). Write to the account.
        - **"business_summary"** (`project_id` or `domain`): what the business does,
          used by Content Studio.

        AI search visibility (read-only here): **"brand_config"**,
        **"brand_overview"**, **"brand_prompts"** (`project_id`; `params.provider`,
        `params.start_date`, `params.end_date`).

        Content Studio:
        - **"article_titles"** (`project_id`, `keyword` or `params.prompt` with
          `params.source_type="prompt"`): title ideas + `content_idea`, free.
        - **"generate_article"** (`project_id`, `params.title`,
          `params.content_idea` from article_titles, plus `keyword`): starts writing
          an article — ⚠️ costs **100 monthly credits**: refused unless
          `confirm_credits=True`, which you only pass after the person agreed.
        - **"get_article"** (`project_id`, `params.article_id`): the article once
          written (poll).

        Args:
            op: see above.
            project_id: from op="list" or op="create".
            domain: the site (create, business_summary).
            keyword: target keyword (article_titles, generate_article).
            language: language code.
            loc_id: Google location id from `ubersuggest_account(op="locations")`.
            params: any other argument, under Ubersuggest's own name.
            confirm_credits: required True for generate_article (100 credits).
        """
        upstream = _op(_PROJECTS_OPS, op)
        if upstream == "generate_article" and not confirm_credits:
            raise _bad("generate_article spends 100 monthly Ubersuggest credits: ask the "
                       "person, then call again with confirm_credits=True.")
        args = _arguments(upstream, {"project_id": project_id, "domain": domain,
                                     "keyword": keyword, "language": language,
                                     "loc_id": loc_id}, params)
        return _call(upstream, args)

    @mcp.tool()
    def ubersuggest_account(
        op: Literal["status", "limits", "locations", "location_details",
                    "validate_site", "blog"] = "status",
        query: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """The connected Ubersuggest account, and lookups the other tools need.

        `op`:
        - **"status"** (default): who is signed in and on which plan — also the
          connection check.
        - **"limits"**: the plan's usage and remaining quotas.
        - **"locations"** (`query`, e.g. "France", "Paris"): Google location ids for
          `loc_id`; `params.lang`, `params.limit`.
        - **"location_details"** (`params.location_ids`): names of given ids.
        - **"validate_site"** (`params.site`): is it a reachable site/domain.
        - **"blog"** (`query`; `params.category`, `params.full_content`): search Neil
          Patel's blog; each text capped at 4,000 characters.

        Args:
            op: see above.
            query: search text (locations, blog).
            params: any other argument, under Ubersuggest's own name.
        """
        upstream = _op(_ACCOUNT_OPS, op)
        return _call(upstream, _arguments(upstream, {"query": query}, params))
