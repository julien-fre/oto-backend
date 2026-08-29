"""Firecrawl — lire une page web comme un humain la voit (firecrawl.dev).

Wrappe `oto.tools.firecrawl.client.FirecrawlClient` (API v2). keyed `api_key`
(Bearer), byo-only (pas de clé plateforme) : chaque user/org connecte SON compte —
Firecrawl se facture au crédit.

Cinq gestes, du moins cher au plus cher :
- `firecrawl_map` : les URLs d'un site, sans le contenu (repérage).
- `firecrawl_scrape` : UNE page en markdown propre (JS exécuté, nav retirée).
- `firecrawl_search` : recherche web + contenu des résultats en un appel.
- `firecrawl_crawl` : un domaine entier — **asynchrone** (`firecrawl_crawl_status`).
- `firecrawl_extract` : données structurées sur N URLs — **asynchrone**.

Face aux voisins : `serper_scrape` rend le HTML brut d'une URL (moins cher, pas de
rendu JS), le connecteur `browser` lit des pages derrière un login. Firecrawl est
le chemin quand il faut du markdown propre à l'échelle d'un site.

Les appels au client sont écrits en clair (`_client().scrape(…)`) et non dispatchés
par nom : c'est ce qui les rend vérifiables par la sonde version-skew.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, url_perimeter
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Firecrawl a rejeté la clé API (HTTP {status}) — vérifie la clé "
                "configurée sur ce connecteur (Firecrawl : Dashboard → API Keys).")
    if status == 402:
        return ("Firecrawl : crédits épuisés ou plan insuffisant (402) — recharge "
                "le compte, ou réduis la portée (limit, formats).")
    if status == 404:
        return f"Firecrawl : ressource introuvable (404) — vérifie l'id du job. {e.body}"
    if status == 429:
        return "Firecrawl : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Firecrawl est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Firecrawl a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : un `map` d'example.com, l'appel authentifié
    le moins coûteux (pas de rendu de page)."""
    from oto.tools.firecrawl.client import FirecrawlClient
    FirecrawlClient(api_key=fields["key"]).map_site("https://example.com", limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.firecrawl.client import FirecrawlClient

    connector_verify.register("firecrawl", _verify)

    def _client() -> FirecrawlClient:
        key, _ = access.resolve_api_key("firecrawl")
        return FirecrawlClient(api_key=key)

    @contextmanager
    def _upstream():
        """Traduit un refus de Firecrawl en erreur d'outil actionnable."""
        try:
            yield
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- repérage -----------------------------------------------------------

    @mcp.tool()
    def firecrawl_map(
        url: str,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        include_subdomains: Optional[bool] = None,
        sitemap: Optional[str] = None,
    ) -> dict:
        """List every URL of a site WITHOUT fetching page content — fast and cheap.

        Use this before crawling: it shows what the site contains, so you can scrape
        only the pages that matter instead of paying for a full crawl.

        Args:
            url: site root (e.g. `https://acme.com`) — refused if under the
                project's `excluded_url_prefixes`, which also drop matching links.
            search: keep only URLs matching this term (e.g. "pricing", "careers").
            limit: max URLs (API default 5000).
            include_subdomains: also list `blog.acme.com` (API default true).
            sitemap: `"include"` (default) | `"skip"` | `"only"`.

        Returns: `{success, links: [{url, title?, description?}]}`.
        """
        per = url_perimeter.perimeter_of_call()
        url_perimeter.refuse_if_excluded(url, per)
        with _upstream():
            result = _client().map_site(url, search=search, limit=limit,
                                        include_subdomains=include_subdomains,
                                        sitemap=sitemap)
        return url_perimeter.filter_results(result, per)

    # --- une page -----------------------------------------------------------

    @mcp.tool()
    def firecrawl_scrape(
        url: str,
        formats: Optional[list] = None,
        only_main_content: Optional[bool] = None,
        include_tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None,
        wait_for: Optional[int] = None,
        actions: Optional[list[dict]] = None,
        max_age: Optional[int] = None,
        mobile: Optional[bool] = None,
        proxy: Optional[str] = None,
    ) -> dict:
        """Fetch ONE page as clean markdown (JavaScript rendered, nav/ads stripped).

        Args:
            url: the page — refused if under the project's `excluded_url_prefixes`.
            formats: outputs wanted, default `["markdown"]`. Also accepts the API's
                object form — `[{"type": "json", "schema": {...}}]` extracts
                structured data in the same call, `[{"type": "screenshot",
                "fullPage": true}]` returns an image, `["links"]` the outgoing links.
            only_main_content: strip nav/footer/ads (default true).
            include_tags / exclude_tags: CSS selectors to keep / drop.
            wait_for: milliseconds to wait before capture (JS-populated pages).
            actions: interactions before capture, e.g.
                `[{"type": "click", "selector": "#accept"}, {"type": "wait",
                "milliseconds": 1000}, {"type": "scroll", "direction": "down"}]` —
                how you get past a cookie wall or a "load more" button.
            max_age: accept a cached version up to N milliseconds old — much faster
                and cheaper than a fresh render when the page is stable.
            proxy: `"basic"` | `"stealth"` | `"auto"` — stealth costs more, use it
                only when a site blocks the basic fetch.

        Returns: `{success, data: {markdown?, html?, links?, screenshot?, json?,
            metadata: {title, description, sourceURL, statusCode, …}}}`.
        """
        url_perimeter.refuse_if_excluded(url, url_perimeter.perimeter_of_call())
        with _upstream():
            return _client().scrape(
                url, formats=formats, only_main_content=only_main_content,
                include_tags=include_tags, exclude_tags=exclude_tags,
                wait_for=wait_for, actions=actions, max_age=max_age,
                mobile=mobile, proxy=proxy)

    # --- recherche ----------------------------------------------------------

    @mcp.tool()
    def firecrawl_search(
        query: str,
        limit: Optional[int] = None,
        sources: Optional[list] = None,
        categories: Optional[list] = None,
        tbs: Optional[str] = None,
        location: Optional[str] = None,
        country: Optional[str] = None,
        include_domains: Optional[list[str]] = None,
        exclude_domains: Optional[list[str]] = None,
        scrape_options: Optional[dict] = None,
    ) -> dict:
        """Search the web and, if asked, return each result's full content.
        Under a project with `excluded_url_prefixes`, matching results are dropped
        and counted.

        Args:
            query: max 500 chars; supports `"exact phrase"`, `-excluded`, `site:`,
                `filetype:`.
            limit: results wanted (default 10, max 100).
            sources: `[{"type": "web"}]` (default), `"news"`, `"images"`.
            categories: `[{"type": "github"|"research"|"pdf"}]`.
            tbs: time filter, e.g. `"qdr:d"` (24h), `"qdr:w"`, `"qdr:m"`.
            include_domains / exclude_domains: restrict / drop domains (mutually
                exclusive on Firecrawl's side).
            scrape_options: pass `{"formats": ["markdown"]}` to get each result's
                page content — without it you only get title/description/url.

        Returns: `{success, data: {web?, news?, images?}, creditsUsed}`.
        """
        with _upstream():
            result = _client().search(
                query, limit=limit, sources=sources, categories=categories,
                tbs=tbs, location=location, country=country,
                include_domains=include_domains, exclude_domains=exclude_domains,
                scrape_options=scrape_options)
        return url_perimeter.filter_results(result, url_perimeter.perimeter_of_call())

    # --- crawl (asynchrone) -------------------------------------------------

    @mcp.tool()
    def firecrawl_crawl(
        url: str,
        limit: Optional[int] = None,
        include_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        max_discovery_depth: Optional[int] = None,
        crawl_entire_domain: Optional[bool] = None,
        allow_subdomains: Optional[bool] = None,
        sitemap: Optional[str] = None,
        delay: Optional[float] = None,
        prompt: Optional[str] = None,
        scrape_options: Optional[dict] = None,
    ) -> dict:
        """Start crawling a whole site. ASYNCHRONOUS — returns a job id.

        Poll `firecrawl_crawl_status(job_id)` until `status == "completed"`. ALWAYS
        set `limit`: the API default is 10000 pages, and each page burns credits.

        Args:
            url: start URL — refused if under the project's `excluded_url_prefixes`
                (matching pages are also dropped from `firecrawl_crawl_status`).
            limit: max pages to crawl — the one guard that caps the bill.
            include_paths / exclude_paths: regexes on the path (e.g. `["/blog/.*"]`).
            max_discovery_depth: how deep to follow links from the start URL.
            crawl_entire_domain: leave the start URL's subtree (default false).
            allow_subdomains: also crawl `blog.acme.com` (default false).
            delay: seconds between requests — slower, but avoids being blocked.
            prompt: plain-language brief Firecrawl turns into crawl options.
            scrape_options: per-page scrape settings, same keys as
                `firecrawl_scrape` but camelCase (e.g. `{"formats": ["markdown"],
                "onlyMainContent": true}`).

        Returns: `{success, id, url}`.
        """
        url_perimeter.refuse_if_excluded(url, url_perimeter.perimeter_of_call())
        with _upstream():
            return _client().crawl(
                url, limit=limit, include_paths=include_paths,
                exclude_paths=exclude_paths, max_discovery_depth=max_discovery_depth,
                crawl_entire_domain=crawl_entire_domain,
                allow_subdomains=allow_subdomains, sitemap=sitemap,
                delay=delay, prompt=prompt, scrape_options=scrape_options)

    @mcp.tool()
    def firecrawl_crawl_status(
        job_id: Optional[str] = None,
        next_url: Optional[str] = None,
    ) -> dict:
        """Check a crawl and read the pages extracted so far.

        Args:
            job_id: id returned by `firecrawl_crawl`.
            next_url: the `next` URL from a previous response. Responses are capped
                at 10 MB, so a large crawl comes in slices — pass `next` back here
                to get the following one (it supersedes `job_id`).

        Returns: `{status: scraping|completed|failed, total, completed, creditsUsed,
            next?, data: [pages]}`.
        """
        with _upstream():
            result = _client().crawl_status(crawl_id=job_id, next_url=next_url)
        return url_perimeter.filter_results(result, url_perimeter.perimeter_of_call())

    @mcp.tool()
    def firecrawl_cancel_crawl(job_id: str) -> dict:
        """Stop a running crawl — halts credit consumption immediately."""
        with _upstream():
            return _client().cancel_crawl(job_id)

    # --- extraction structurée (asynchrone) ---------------------------------

    @mcp.tool()
    def firecrawl_extract(
        urls: list[str],
        prompt: Optional[str] = None,
        schema: Optional[dict] = None,
        enable_web_search: Optional[bool] = None,
        show_sources: Optional[bool] = None,
    ) -> dict:
        """Extract structured data across several pages. ASYNCHRONOUS — returns a job id.

        Poll `firecrawl_extract_status(job_id)`. For a single page, a
        `firecrawl_scrape` with `formats=[{"type": "json", "schema": {...}}]` is
        synchronous and cheaper.

        Args:
            urls: pages to read; a trailing `/*` widens to the whole site
                (e.g. `["https://acme.com/*"]`). The batch is refused if one is
                under the project's `excluded_url_prefixes`.
            prompt: what you're looking for, in plain language.
            schema: JSON Schema of the wanted output — far more reliable than a
                prompt alone, and it makes the result directly usable.
            enable_web_search: let Firecrawl look beyond the given URLs.

        Returns: `{success, id}`.
        """
        url_perimeter.refuse_if_any_excluded(urls, url_perimeter.perimeter_of_call())
        with _upstream():
            return _client().extract(
                urls, prompt=prompt, schema=schema,
                enable_web_search=enable_web_search, show_sources=show_sources)

    @mcp.tool()
    def firecrawl_extract_status(job_id: str) -> Any:
        """Read an extraction job: `{success, status, data?, sources?}`."""
        with _upstream():
            return _client().extract_status(job_id)
