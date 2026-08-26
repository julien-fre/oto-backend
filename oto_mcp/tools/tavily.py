"""Tavily — recherche web et lecture de pages taillées pour un agent (tavily.com).

Wrappe `oto.tools.tavily.client.TavilyClient`. keyed `api_key` (Bearer `tvly-…`),
byo user/org **et** clé plateforme ouverte (quota 100/mois — garde conservatrice,
cf. providers.py) : la recherche web est un socle, on ne fait pas payer le ticket
d'entrée.

Quatre gestes, tous synchrones :
- `tavily_search` : recherche web → extraits cités + réponse synthétique optionnelle.
- `tavily_extract` : N URLs → markdown propre, reclassé par une intention.
- `tavily_map` : les URLs d'un site, sans contenu (repérage, peu cher).
- `tavily_crawl` : un site guidé en langage naturel → contenu des pages.

Face aux voisins : `serper_search` rend le SERP Google brut (positions, PAA),
`firecrawl_scrape` rend UNE page avec JS exécuté et `firecrawl_crawl` tient les
gros crawls (asynchrone). Tavily est le chemin quand il faut *une réponse sourcée*
en un appel, ou le contenu propre de quelques URLs sans rendu lourd.

Les appels au client sont écrits en clair (`_client().search(…)`) et non dispatchés
par nom : c'est ce qui les rend vérifiables par la sonde version-skew.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional, Union

import requests
from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

# Le chemin REST d'invocation d'outil coupe à 45 s (`api_routes.py`) : un crawl
# synchrone doit rendre avant. Au-delà, c'est `firecrawl_crawl` (job asynchrone).
_CRAWL_TIMEOUT_S = 40
_CRAWL_MAX_PAGES = 100


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Tavily a rejeté la clé API (HTTP {status}) — vérifie la clé "
                "configurée sur ce connecteur (Tavily : app.tavily.com → API Keys).")
    if status == 429:
        return "Tavily : trop de requêtes (429) — réessaie dans un instant."
    if status == 432:
        return ("Tavily : plafond du plan atteint (432) — les crédits du mois sont "
                "épuisés, recharge le compte ou passe au plan supérieur.")
    if status == 433:
        return ("Tavily : plafond pay-as-you-go atteint (433) — relève la limite de "
                "dépense dans le compte Tavily.")
    if status == 400:
        return f"Tavily : requête invalide (400) — {e.body}"
    if status in (500, 502, 503, 504):
        return f"Tavily est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Tavily a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : une recherche `basic` à 1 résultat — l'appel
    authentifié le moins coûteux (1 crédit ; `map` en coûte autant et prend plus
    de temps)."""
    from oto.tools.tavily.client import TavilyClient
    TavilyClient(api_key=fields["key"]).search("tavily", max_results=1,
                                               search_depth="basic", timeout=20)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.tavily.client import TavilyClient

    connector_verify.register("tavily", _verify)

    def _client() -> TavilyClient:
        key, _ = access.resolve_api_key("tavily")
        return TavilyClient(api_key=key)

    @contextmanager
    def _upstream():
        """Traduit un refus de Tavily en erreur d'outil actionnable."""
        try:
            yield
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except (requests.ConnectionError, requests.Timeout) as e:
            raise _bad(f"Tavily injoignable (réseau/timeout) — réessaie plus tard. {e}")

    # --- recherche ----------------------------------------------------------

    @mcp.tool()
    def tavily_search(
        query: str,
        topic: Optional[str] = None,
        search_depth: Optional[str] = None,
        max_results: Optional[int] = None,
        time_range: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_answer: Optional[Union[bool, str]] = "basic",
        include_raw_content: Optional[Union[bool, str]] = None,
        include_domains: Optional[list[str]] = None,
        exclude_domains: Optional[list[str]] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
    ) -> dict:
        """Search the web and get cited snippets plus a short synthesized answer.

        Best default for "find out X about Y": one call returns an `answer` grounded
        in the `results` (title, url, content excerpt, relevance score). Use
        `serper_search` instead when you need the raw Google SERP (rankings, PAA).

        Args:
            query: max 400 chars, plain language works best.
            topic: `general` (default) | `news` (recent, dated sources) | `finance`.
            search_depth: `basic` (default, 1 credit) | `advanced` (2 credits, better
                relevance and up to 3 excerpts per source) | `fast` | `ultra-fast`.
            max_results: 0-20 (default 5).
            time_range: `day` | `week` | `month` | `year`.
            start_date / end_date: `YYYY-MM-DD` bounds (publication date).
            include_answer: `"basic"` (default) | `"advanced"` (longer) | `False`.
            include_raw_content: `"markdown"` | `"text"` — full page content of
                every result; heavy, prefer `tavily_extract` on the URLs you keep.
            include_domains / exclude_domains: restrict / drop domains.
            country: English country name (e.g. `france`) to boost local results.
            language: ISO 639-1 code (e.g. `fr`).

        Returns: `{query, answer, results: [{title, url, content, score}],
            response_time, usage: {credits}}`.
        """
        with _upstream():
            return _client().search(
                query, search_depth=search_depth, topic=topic,
                max_results=max_results, time_range=time_range,
                start_date=start_date, end_date=end_date,
                include_answer=include_answer,
                include_raw_content=include_raw_content,
                include_domains=include_domains, exclude_domains=exclude_domains,
                country=country, language=language)

    # --- lecture d'URLs -----------------------------------------------------

    @mcp.tool()
    def tavily_extract(
        urls: list[str],
        query: Optional[str] = None,
        extract_depth: Optional[str] = None,
        chunks_per_source: Optional[int] = None,
        format: Optional[str] = None,
        include_images: Optional[bool] = None,
    ) -> dict:
        """Read one or more web pages as clean markdown (up to 20 URLs per call).

        Cheap way to get the full content of pages you already know (e.g. the URLs
        returned by `tavily_search`). Natively batch: pass all URLs at once — failed
        ones come back in `failed_results`, the rest still succeed.

        Args:
            urls: 1-20 URLs.
            query: intent used to rerank the excerpts (e.g. "pricing tiers").
            extract_depth: `basic` (default, 1 credit / 5 URLs) | `advanced`
                (2 credits / 5 URLs — tables, dynamic content).
            chunks_per_source: 1-5 excerpts per page (only with `query`).
            format: `markdown` (default) | `text`.

        Returns: `{results: [{url, raw_content}], failed_results: [{url, error}],
            usage: {credits}}`.
        """
        if not urls:
            raise _bad("urls : au moins une URL")
        if len(urls) > 20:
            raise _bad("urls : 20 URLs maximum par appel")
        with _upstream():
            return _client().extract(
                urls, query=query, extract_depth=extract_depth,
                chunks_per_source=chunks_per_source, format=format,
                include_images=include_images, timeout_s=_CRAWL_TIMEOUT_S)

    # --- repérage d'un site -------------------------------------------------

    @mcp.tool()
    def tavily_map(
        url: str,
        instructions: Optional[str] = None,
        max_depth: Optional[int] = None,
        max_breadth: Optional[int] = None,
        limit: Optional[int] = None,
        select_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        allow_external: Optional[bool] = False,
    ) -> dict:
        """List a site's URLs WITHOUT fetching content — do this before a crawl.

        Args:
            url: site root (e.g. `https://acme.com`).
            instructions: natural-language focus ("product and pricing pages") —
                doubles the cost.
            max_depth: 1-5 levels from the root (default 1).
            max_breadth: links followed per level, 1-500 (default 20).
            limit: max pages processed (default 50, capped at 100 here).
            select_paths / exclude_paths: regex on URL paths (`["/blog/.*"]`).
            allow_external: follow links to other domains (default false here).

        Returns: `{base_url, results: [url, …], usage: {credits}}`.
        """
        limit = _cap_limit(limit)
        with _upstream():
            return _client().map_site(
                url, instructions=instructions, max_depth=max_depth,
                max_breadth=max_breadth, limit=limit, select_paths=select_paths,
                exclude_paths=exclude_paths, allow_external=allow_external,
                # budget Tavily 40 s ⟹ le HTTP local n'attend jamais les 160 s
                # du défaut client (contrainte mono-loop, cf. conventions)
                timeout_s=_CRAWL_TIMEOUT_S, timeout=45)

    # --- crawl (synchrone, borné) -------------------------------------------

    @mcp.tool()
    def tavily_crawl(
        url: str,
        instructions: Optional[str] = None,
        max_depth: Optional[int] = None,
        max_breadth: Optional[int] = None,
        limit: Optional[int] = None,
        select_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        allow_external: Optional[bool] = False,
        extract_depth: Optional[str] = None,
        format: Optional[str] = None,
    ) -> dict:
        """Crawl a site and return page content, guided by a natural-language brief.

        Synchronous and bounded (max 100 pages, 40 s): right for "read the docs
        section of this site" or "get every case study". For whole-domain crawls use
        `firecrawl_crawl` (asynchronous, no page cap).

        Args:
            url: root URL.
            instructions: what to look for ("pricing and plan comparison pages") —
                Tavily follows only relevant links; doubles the cost.
            max_depth: 1-5 (default 1). max_breadth: 1-500 links per level (default 20).
            limit: max pages (default 50, capped at 100 here).
            select_paths / exclude_paths: regex on URL paths.
            allow_external: follow links to other domains (default false here).
            extract_depth: `basic` (default, 1 credit / 10 pages) | `advanced`
                (2 credits / 10 pages).
            format: `markdown` (default) | `text`.

        Returns: `{base_url, results: [{url, raw_content}], usage: {credits}}` —
            `raw_content` is `null` for pages Tavily reached but could not extract
            (seen live: reference pages rendered client-side); skip those.
        """
        limit = _cap_limit(limit)
        with _upstream():
            return _client().crawl(
                url, instructions=instructions, max_depth=max_depth,
                max_breadth=max_breadth, limit=limit, select_paths=select_paths,
                exclude_paths=exclude_paths, allow_external=allow_external,
                extract_depth=extract_depth, format=format,
                timeout_s=_CRAWL_TIMEOUT_S, timeout=45)


def _cap_limit(limit: Optional[int]) -> int:
    """Borne le nombre de pages d'un map/crawl synchrone (défaut API : 50)."""
    if limit is None:
        return 50
    if limit < 1:
        raise _bad("limit : au moins 1 page")
    return min(limit, _CRAWL_MAX_PAGES)
