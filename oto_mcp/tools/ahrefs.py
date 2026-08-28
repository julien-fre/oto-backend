"""Ahrefs — SEO data: backlinks, keywords, rank tracking, site audits, brand
visibility across AI chatbots, on-site analytics, GSC, social publishing.

Wrappe `oto.tools.ahrefs.client.AhrefsClient` (API v3, Bearer). keyed
`api_key`, byo-only (pas de clé plateforme) : chaque org pose SA clé Ahrefs —
un seat Ahrefs est cher et par abonnement, même raisonnement que TheirStack.

**Surface consolidée (ADR 0047 §Amendement)** : Ahrefs expose ~150 opérations
REST distinctes. Elles se groupent par PRODUIT en un tool avec un axe
`report=`/`op=` plutôt qu'un tool par endpoint (comme `serpapi_search(engine=)`
pour ses 40+ moteurs) : `ahrefs_site_explorer`, `ahrefs_keywords_explorer`,
`ahrefs_site_audit`, `ahrefs_rank_tracker` couvrent 44 endpoints à 4 tools.
`ahrefs_web_analytics`/`ahrefs_gsc` restent larges (34/12 valeurs de `report`)
car strictement homogènes — un seul paramètre change de nom d'un rapport à
l'autre (le filtre de série du chart Web Analytics), porté par `extra`.

**Aucun param n'est retenu au silence** : un `report`/`op` qui ne reconnaît pas
un argument fourni REFUSE (jamais un drop silencieux qui rendrait un résultat
plausible mais faux — leçon silae). Le `select`/`where`/`order_by` d'Ahrefs
(sa propre « filter syntax », doc `/api/docs/filter-syntax`) n'est PAS retypé
en dizaines de champs : passthrough sur les noms Ahrefs eux-mêmes, comme la
DSL TheirStack. Colonnes `select` par défaut posées seulement pour les
rapports où les identifiants valides ont été vérifiés en doc (voir
`_DEFAULT_SELECT`) — ailleurs `select` reste requis tel quel, jamais deviné.

**Écritures : lecture + création exposées, suppression/patch NON exposées**
(guide Silae — `tools/silae.py`) : `AhrefsClient` porte les 22 endpoints
Management en entier (delete_projects, update_project,
delete_project_keywords, untag_project_keywords, delete_project_competitors,
delete_keyword_list_keywords, delete_brand_radar_prompts,
update_brand_radar_report), mais AUCUN tool d'ici ne les atteint — supprimer
un projet Rank Tracker ou republier un Brand Radar report est un acte
délibéré, pas un effet de bord de la couverture client. Idem Social Media :
`ahrefs_social(op="publish")` publie, mais `delete_social_post`/
`update_social_post` restent client-only.

**Provenance de la vérité terrain (2026-08-20, aucune clé API en main) :**
Ahrefs publie un spec OpenAPI 3.2.0 machine-readable à
`https://docs.ahrefs.com/openapi.json` (pas de lien direct depuis les pages de
doc — trouvé après un essai raté sur `/v3/openapi.json`). Tous les noms de
paramètres, `required`, formes de corps POST/PUT/PATCH, et colonnes `select`
valides (avec leur coût en unités, tiré des `description` du schéma de
réponse) de ce module sont vérifiés MOT POUR MOT contre ce spec — pas contre
un résumé de page doc par un petit modèle (la première passe de recherche
utilisait WebFetch, dont le résumé s'est révélé fiable sur les noms de
colonnes mais avait raté trois formes de corps réelles : `project-keywords`
PUT veut deux tableaux PARALLÈLES `keywords`+`locations` — pas un seul tableau
enrichi ; `project-keywords-tags` PUT veut `project_id` dans le corps, pas en
query ; `brand-radar-reports` POST n'a PAS de champ `data_source`/`frequency`
séparé, seul `prompts_frequency[]` en porte — les trois corrigés ici, chacun
verrouillé par un test). Ce qui reste NON vérifié : aucun appel n'a été fait à
la vraie API — le spec dit ce qu'Ahrefs DOCUMENTE accepter, pas ce qu'il
accepte réellement en prod. Un premier appel réel doit être fait avant de
faire confiance aveuglément à un comportement non couvert par un test.

Coût : la plupart des rapports défaultent à `limit=1000` lignes côté Ahrefs si
omis ; `site-audit/issues` et `site-audit/page-content` coûtent 50 unités/
requête quel que soit `limit`. `ahrefs_account()` (gratuit) donne la
consommation réelle — c'est aussi la sonde de connexion.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# --- select par défaut, uniquement pour les rapports dont les colonnes ont
# été vérifiées en doc (docs.ahrefs.com, 2026-08-20) — ailleurs `select` reste
# un param requis tel quel, jamais deviné (cf. docstring module).
# ⚠️ Certaines colonnes par défaut sont FACTURÉES au-delà du coût de base de la
# requête (constaté en doc, précision variable selon l'endpoint) : `traffic`/
# `traffic_domain`/`sum_traffic`-like (site_explorer refdomains/organic-competitors,
# rank_tracker overview, serp_overview) et la plupart des colonnes de top-pages.
# Un agent qui laisse `select` vide sur ces reports paie donc plus qu'un choix de
# colonnes minimal — délibéré (ce sont les colonnes les plus utiles), mais à savoir
# avant de scaler le volume de lignes (`limit`) sur ces reports précis.
_DEFAULT_SELECT: Dict[tuple, str] = {
    ("site_explorer", "organic-keywords"):
        "keyword,volume,best_position,best_position_url,keyword_difficulty,cpc,sum_traffic",
    ("site_explorer", "top-pages"): "url,sum_traffic,top_keyword,top_keyword_volume,keywords",
    ("site_explorer", "all-backlinks"):
        "url_from,url_to,anchor,domain_rating_source,is_dofollow,first_seen",
    ("site_explorer", "refdomains"): "domain,domain_rating,dofollow_links,first_seen,traffic_domain",
    ("site_explorer", "anchors"): "anchor,refdomains,dofollow_links,first_seen",
    ("site_explorer", "organic-competitors"): "competitor_domain,domain_rating,keywords_common,traffic",
    ("site_explorer", "pages-by-backlinks"):
        "url_to,refdomains_target,links_to_target,url_rating_target",
    ("keywords_explorer", "overview"): "keyword,volume,difficulty,cpc,parent_topic,serp_features",
    ("keywords_explorer", "matching-terms"): "keyword,volume,difficulty,cpc,parent_topic",
    ("keywords_explorer", "related-terms"): "keyword,volume,difficulty,cpc",
    ("rank_tracker", "overview"): "keyword,position,volume,traffic,url",
    ("serp_overview", "serp-overview"): "position,url,title,domain_rating,traffic,type",
}

# report → (méthode AhrefsClient, params requis pour CE report). Les params
# optionnels ne sont PAS listés : tout ce qui est fourni et non `None` passe,
# Ahrefs validant lui-même un param hors-sujet pour un report donné.
_SITE_EXPLORER_REPORTS: Dict[str, tuple] = {
    "domain-rating": ("domain_rating", ("target", "date")),
    "backlinks-stats": ("backlinks_stats", ("target", "date")),
    "outlinks-stats": ("outlinks_stats", ("target",)),
    "metrics": ("site_metrics", ("target", "date")),
    "ai-responses-count": ("ai_responses_count", ("target", "select")),
    "refdomains-history": ("refdomains_history", ("target", "date_from")),
    "domain-rating-history": ("domain_rating_history", ("target", "date_from")),
    "url-rating-history": ("url_rating_history", ("target", "date_from")),
    "pages-history": ("pages_history", ("target", "date_from")),
    "metrics-history": ("metrics_history", ("target", "date_from")),
    "keywords-history": ("keywords_history", ("target", "date_from")),
    "metrics-by-country": ("metrics_by_country", ("target", "date")),
    "pages-by-traffic": ("pages_by_traffic", ("target",)),
    "total-search-volume-history": ("total_search_volume_history", ("target", "date_from")),
    "all-backlinks": ("all_backlinks", ("target", "select")),
    "broken-backlinks": ("broken_backlinks", ("target", "select")),
    "refdomains": ("refdomains", ("target", "select")),
    "anchors": ("anchors", ("target", "select")),
    "organic-keywords": ("organic_keywords", ("target", "select", "date")),
    "organic-competitors": ("organic_competitors", ("target", "country", "date", "select")),
    "top-pages": ("top_pages", ("target", "select", "date")),
    "paid-pages": ("paid_pages", ("target", "select", "date")),
    "pages-by-backlinks": ("pages_by_backlinks", ("target", "select")),
    "pages-by-internal-links": ("pages_by_internal_links", ("target", "select")),
    "crawled-pages": ("crawled_pages", ("target", "select")),
    "linkeddomains": ("linked_domains", ("target", "select")),
    "linked-anchors-external": ("linked_anchors_external", ("target", "select")),
    "linked-anchors-internal": ("linked_anchors_internal", ("target", "select")),
}

# Reports documented as NOT taking `country` (verified per-endpoint against
# docs.ahrefs.com, 2026-08-20). `_call_report` otherwise forwards any supplied
# optional param verbatim and lets Ahrefs validate it — right for params whose
# absence just means "not filtered", WRONG for `country`, whose absence changes
# MEANING (global vs one-market numbers): a report that silently ignores an
# unsupported `country` would return a plausible-but-wrong global figure while
# the agent believes it scoped to one market. Guarded explicitly instead of
# trusting upstream to reject it — the highest-value case of this param class
# (mode/protocol/volume_mode/traffic_mode share the same risk but are NOT
# guarded here; still forwarded and left to Ahrefs).
_SITE_EXPLORER_NO_COUNTRY = frozenset({
    "domain-rating", "backlinks-stats", "outlinks-stats", "refdomains-history",
    "domain-rating-history", "url-rating-history", "metrics-by-country",
    "all-backlinks", "broken-backlinks", "refdomains", "anchors",
    "pages-by-backlinks", "pages-by-internal-links", "crawled-pages",
    "linkeddomains", "linked-anchors-external", "linked-anchors-internal",
})

_KEYWORDS_EXPLORER_REPORTS: Dict[str, tuple] = {
    "overview": ("keywords_overview", ("select", "country")),
    "volume-history": ("volume_history", ("keyword", "country")),
    "volume-by-country": ("volume_by_country", ("keyword",)),
    "matching-terms": ("matching_terms", ("select", "country")),
    "related-terms": ("related_terms", ("select", "country")),
    "search-suggestions": ("search_suggestions", ("select", "country")),
}

_SITE_AUDIT_REPORTS: Dict[str, tuple] = {
    "projects": ("audit_projects", ()),
    "issues": ("audit_issues", ("project_id",)),
    "page-content": ("audit_page_content", ("project_id", "target_url", "select")),
    "page-explorer": ("audit_page_explorer", ("project_id",)),
}

_RANK_TRACKER_REPORTS: Dict[str, tuple] = {
    "overview": ("rank_overview", ("project_id", "date", "device", "select")),
    "serp-overview": ("rank_serp_overview", ("project_id", "keyword", "country", "device")),
    "competitors-overview": ("rank_competitors_overview", ("project_id", "date", "device", "select")),
    "competitors-pages": ("rank_competitors_pages", ("project_id", "date", "device", "select")),
    "competitors-domains": ("rank_competitors_domains", ("project_id", "date", "device", "select")),
    "competitors-stats": ("rank_competitors_stats", ("project_id", "date", "device", "select")),
}

# Brand Radar data : GET pour 9 rapports, POST (data_source/select en LISTE,
# pas en chaîne) pour les 2 rapports POST-only.
_BRAND_RADAR_GET_REPORTS: Dict[str, tuple] = {
    "ai-responses": ("brand_ai_responses", ("data_source", "select")),
    "cited-pages": ("brand_cited_pages", ("data_source", "select")),
    "cited-domains": ("brand_cited_domains", ("data_source", "select")),
    "impressions-overview": ("brand_impressions_overview", ("data_source", "select")),
    "mentions-overview": ("brand_mentions_overview", ("data_source", "select")),
    "sov-overview": ("brand_sov_overview", ("data_source",)),
    "impressions-history": ("brand_impressions_history", ("brand", "data_source", "date_from")),
    "mentions-history": ("brand_mentions_history", ("brand", "data_source", "date_from")),
    "sov-history": ("brand_sov_history", ("data_source", "date_from")),
}
_BRAND_RADAR_POST_REPORTS: Dict[str, tuple] = {
    "citations-overview": ("brand_citations_overview", ("data_source", "select")),
    "citations-history": ("brand_citations_history", ("data_source", "date_from")),
}

_GSC_REPORTS = (
    "performance-history", "positions-history", "pages-history", "performance-by-device",
    "metrics-by-country", "ctr-by-position", "performance-by-position", "keyword-history",
    "keywords", "page-history", "pages", "anonymous-queries",
)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Ahrefs a rejeté la clé API (HTTP {status}) — vérifie la clé posée sur ce "
                "connecteur (Ahrefs : Account → API Access).")
    if status == 400:
        return (f"Ahrefs a refusé la requête (HTTP 400) : {e.body} — vérifie les identifiants "
                "de `select`/`where`/`order_by` (chaque endpoint a ses propres colonnes valides, "
                "voir docs.ahrefs.com) et le format `date` (YYYY-MM-DD).")
    if status == 429:
        return "Ahrefs : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Ahrefs est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Ahrefs a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : `limits-and-usage`, gratuit et authentifié."""
    from oto.tools.ahrefs.client import AhrefsClient
    AhrefsClient(api_key=fields["key"]).subscription_info_limits_and_usage()


def _call_report(client, report: str, table: Dict[str, tuple], supplied: Dict[str, Any],
                  extra: Optional[dict]) -> Any:
    """Dispatch générique report→méthode : REFUSE un report inconnu, un param
    fourni non pertinent pour CE report, ou un required manquant — jamais un
    silence qui rendrait un résultat plausible mais faux (leçon silae)."""
    entry = table.get(report)
    if entry is None:
        raise _bad(f"report={report!r} inconnu — valides: {sorted(table)}")
    method_name, required = entry
    kwargs = {k: v for k, v in supplied.items() if v is not None}
    missing = [r for r in required if r not in kwargs]
    if missing:
        raise _bad(f"report={report!r} requiert {', '.join(missing)}")
    if extra:
        if not isinstance(extra, dict):
            raise _bad("`extra` doit être un dict de params Ahrefs (passthrough).")
        kwargs.update(extra)
    return getattr(client, method_name)(**kwargs)


def register(mcp: FastMCP) -> None:
    from oto.tools.ahrefs.client import AhrefsClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("ahrefs", _verify)

    def _client() -> AhrefsClient:
        key, _ = access.resolve_api_key("ahrefs")
        return AhrefsClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Site Explorer — backlinks, organic/paid search, pages, outgoing links.
    # ================================================================

    @mcp.tool()
    def ahrefs_site_explorer(
        report: Literal[
            "domain-rating", "backlinks-stats", "outlinks-stats", "metrics",
            "ai-responses-count", "refdomains-history", "domain-rating-history",
            "url-rating-history", "pages-history", "metrics-history", "keywords-history",
            "metrics-by-country", "pages-by-traffic", "total-search-volume-history",
            "all-backlinks", "broken-backlinks", "refdomains", "anchors",
            "organic-keywords", "organic-competitors", "top-pages", "paid-pages",
            "pages-by-backlinks", "pages-by-internal-links", "crawled-pages",
            "linkeddomains", "linked-anchors-external", "linked-anchors-internal",
        ],
        target: str,
        select: Optional[str] = None,
        date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        country: Optional[str] = None,
        mode: Optional[str] = None,
        protocol: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Ahrefs Site Explorer — one target's backlink profile, organic/paid
        search footprint, pages and outgoing links, by `report`:

        - **domain-rating**/**backlinks-stats**/**metrics**/**metrics-by-country**:
          point-in-time summary metrics (need `date`).
        - **outlinks-stats**/**pages-by-traffic**: summary, no `date` needed.
        - **\\*-history** (refdomains/domain-rating/url-rating/pages/metrics/keywords/
          total-search-volume): trend over time (need `date_from`).
        - **ai-responses-count**: citations of target across AI platforms (need `select`).
        - **all-backlinks**/**broken-backlinks**/**refdomains**/**anchors**: the
          backlink profile itself (need `select` — default select provided for
          `all-backlinks`/`refdomains`/`anchors`, see below).
        - **organic-keywords**/**organic-competitors**/**top-pages**/**paid-pages**:
          organic/paid search footprint (need `select`, `date`; defaults provided
          for organic-keywords/organic-competitors/top-pages).
        - **pages-by-backlinks**/**pages-by-internal-links**/**crawled-pages**: page
          inventory (need `select`; default provided for pages-by-backlinks).
        - **linkeddomains**/**linked-anchors-external**/**linked-anchors-internal**:
          outgoing links (need `select`).

        Args:
            report: which Site Explorer report to run — see above.
            target: domain or URL to analyze (every report needs this).
            select: comma-separated columns to return — REQUIRED for most reports
                (Ahrefs rejects an omitted `select` where it applies); a sensible
                default is used for organic-keywords/top-pages/all-backlinks/
                refdomains/anchors/organic-competitors/pages-by-backlinks when
                omitted. Valid columns are report-specific — see docs.ahrefs.com.
            date: reporting date, YYYY-MM-DD — required by point-in-time reports.
            date_from/date_to: history window, YYYY-MM-DD — required by *-history
                reports (`date_to` optional, defaults to now).
            country: ISO 3166-1 alpha-2 country code.
            mode: 'exact'|'prefix'|'domain'|'subdomains' (default subdomains).
            protocol: 'both'|'http'|'https' (default both).
            where: Ahrefs filter-syntax expression over `select` columns.
            order_by: sort spec, e.g. "volume:desc" or "field_a,field_b:asc".
            limit: max rows (Ahrefs defaults to 1000 if omitted — pass explicitly
                to bound spend on paid reports).
            extra: any other Ahrefs param for this report (e.g. `history`,
                `aggregation`, `history_grouping`, `volume_mode`, `traffic_mode`,
                `date_compared`, `page_positions`, `top_positions`, `timeout`) —
                merged last, overrides the typed args above.
        """
        if country is not None and report in _SITE_EXPLORER_NO_COUNTRY:
            raise _bad(f"report={report!r} n'a pas de paramètre `country` — "
                       "voir docs.ahrefs.com pour ce report.")
        if select is None:
            select = _DEFAULT_SELECT.get(("site_explorer", report))
        supplied = dict(target=target, select=select, date=date, date_from=date_from,
                         date_to=date_to, country=country, mode=mode, protocol=protocol,
                         where=where, order_by=order_by, limit=limit)
        return _run(lambda: _call_report(_client(), report, _SITE_EXPLORER_REPORTS, supplied, extra))

    # ================================================================
    # Keywords Explorer — keyword metrics + ideas.
    # ================================================================

    @mcp.tool()
    def ahrefs_keywords_explorer(
        report: Literal["overview", "volume-history", "volume-by-country",
                         "matching-terms", "related-terms", "search-suggestions"],
        country: str,
        select: Optional[str] = None,
        keyword: Optional[str] = None,
        keywords: Optional[str] = None,
        keyword_list_id: Optional[int] = None,
        target: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Ahrefs Keywords Explorer — metrics for known keywords, or ideas from a seed.

        - **overview**: volume/difficulty/clicks/SERP-features for `keywords`,
          `keyword_list_id`, or ranking keywords of `target`.
        - **volume-history**: monthly volume history for ONE `keyword`.
        - **volume-by-country**: `keyword`'s average volume broken down by country.
        - **matching-terms**/**related-terms**/**search-suggestions**: keyword ideas
          seeded from `keywords`/`keyword_list_id` (need `select`; defaults
          provided for overview/matching-terms/related-terms).

        Args:
            report: which Keywords Explorer report to run.
            country: ISO 3166-1 alpha-2 country code (every report needs this,
                except volume-history/volume-by-country which key off `keyword` alone).
            select: comma-separated columns — default provided for
                overview/matching-terms/related-terms; required as-is elsewhere.
            keyword: single keyword — required by volume-history/volume-by-country.
            keywords: comma-separated seed keywords (overview/matching-terms/
                related-terms/search-suggestions).
            keyword_list_id: seed from an existing Ahrefs keyword list instead.
            target: seed overview from a domain/URL's ranking keywords instead.
            where/order_by/limit: filter-syntax expression / sort / row cap
                (max 150000 on matching-terms/related-terms/search-suggestions).
            extra: any other param (`terms`, `match_mode`, `view_for`,
                `target_position`, `mode`, `date_from`/`date_to` for volume-history,
                `volume_monthly_date_from`/`_to` for overview) — merged last.
        """
        if select is None:
            select = _DEFAULT_SELECT.get(("keywords_explorer", report))
        supplied = dict(country=country, select=select, keyword=keyword, keywords=keywords,
                         keyword_list_id=keyword_list_id, target=target, where=where,
                         order_by=order_by, limit=limit)
        return _run(lambda: _call_report(_client(), report, _KEYWORDS_EXPLORER_REPORTS,
                                          supplied, extra))

    # ================================================================
    # Site Audit — crawl health, issues, page content/explorer.
    # ================================================================

    @mcp.tool()
    def ahrefs_site_audit(
        report: Literal["projects", "issues", "page-content", "page-explorer"],
        project_id: Optional[int] = None,
        select: Optional[str] = None,
        date: Optional[str] = None,
        target_url: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Ahrefs Site Audit — crawl-based technical SEO for a Site Audit project.

        - **projects**: health scores of all reachable projects (`project_id` optional filter).
        - **issues**: issues found in a project's latest crawl. 50 units/request.
        - **page-content**: raw text/HTML/metadata of ONE crawled page (need
          `target_url`, `select` from crawl_datetime/page_text/page_text_md/
          raw_html/rendered_html). 50 units/request.
        - **page-explorer**: per-page crawl metrics (indexability, links, structured data…).

        Args:
            report: which Site Audit report to run.
            project_id: Site Audit project id (from its Ahrefs URL) — required by
                issues/page-content/page-explorer, optional filter on projects.
            select: required by page-content; optional elsewhere.
            date: crawl date, YYYY-MM-DDThh:mm:ss (UTC) — defaults to latest crawl.
            target_url: page to fetch — required by page-content.
            where/order_by/limit: filter/sort/cap — page-explorer only.
            extra: `project_name`/`project_url` (projects filter), `date_compared`
                (issues/page-explorer), `filter_mode`/`issue_id`/`offset` (page-explorer).
        """
        supplied = dict(project_id=project_id, select=select, date=date, target_url=target_url,
                         where=where, order_by=order_by, limit=limit)
        return _run(lambda: _call_report(_client(), report, _SITE_AUDIT_REPORTS, supplied, extra))

    # ================================================================
    # Rank Tracker — tracked-keyword positions for a Rank Tracker project.
    # ================================================================

    @mcp.tool()
    def ahrefs_rank_tracker(
        report: Literal["overview", "serp-overview", "competitors-overview",
                         "competitors-pages", "competitors-domains", "competitors-stats"],
        project_id: int,
        date: Optional[str] = None,
        device: Optional[str] = None,
        select: Optional[str] = None,
        keyword: Optional[str] = None,
        country: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Ahrefs Rank Tracker — positions/traffic for keywords tracked in a project.
        `project_id` comes from `ahrefs_project(op="list")`.

        - **overview**: tracked-keyword positions/traffic/SERP-features (need
          `date`, `device`, `select` — default select provided).
        - **serp-overview**: SERP for ONE tracked `keyword` (need `country`, `device`).
        - **competitors-overview**/**-pages**/**-domains**/**-stats**: competitor
          rankings/pages/domains/position-distribution on tracked keywords (need
          `date`, `device`, `select`).

        Args:
            report: which Rank Tracker report to run.
            project_id: Rank Tracker project id.
            date: reporting date, YYYY-MM-DD — required except serp-overview.
            device: 'desktop'|'mobile' — required by every report.
            select: comma-separated columns — required except serp-overview;
                default provided for overview.
            keyword: required by serp-overview.
            country: required by serp-overview (ISO 3166-1 alpha-2).
            where/order_by/limit: filter/sort/cap (not on competitors-stats/serp-overview).
            extra: `date_compared`, `volume_mode`, `top_positions` (serp-overview),
                `location_id`/`language_code` (serp-overview, from `ahrefs_locations`),
                `target_and_tracked_competitors_only` (competitors-pages/-domains).
        """
        if select is None:
            select = _DEFAULT_SELECT.get(("rank_tracker", report))
        supplied = dict(project_id=project_id, date=date, device=device, select=select,
                         keyword=keyword, country=country, where=where, order_by=order_by,
                         limit=limit)
        return _run(lambda: _call_report(_client(), report, _RANK_TRACKER_REPORTS, supplied, extra))

    # ================================================================
    # SERP Overview (standalone) — any keyword, no Rank Tracker project needed.
    # ================================================================

    @mcp.tool()
    def ahrefs_serp_overview(
        keyword: str,
        country: str,
        select: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        top_positions: Optional[int] = None,
        date: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """SERP positions for ANY keyword — no Rank Tracker project required
        (unlike `ahrefs_rank_tracker(report="serp-overview")`, which is scoped to
        keywords already tracked in a project).

        Args:
            keyword: the search term to analyze.
            country: ISO 3166-1 alpha-2 market code.
            select: comma-separated columns (default:
                position,url,title,domain_rating,traffic,type).
            type: filter to a SERP feature category, e.g. 'organic', 'paid_top',
                'snippet', 'image', 'knowledge_card', 'local_pack', 'news'.
            top_positions: cap organic results returned (omit = all positions).
            date: SERP snapshot timestamp, YYYY-MM-DDThh:mm:ss (omit = latest).
            extra: any other Ahrefs param, merged last.
        """
        if select is None:
            select = _DEFAULT_SELECT[("serp_overview", "serp-overview")]
        kwargs = {k: v for k, v in dict(type=type, top_positions=top_positions, date=date).items()
                  if v is not None}
        if extra:
            kwargs.update(extra)
        return _run(lambda: _client().serp_overview(keyword=keyword, country=country,
                                                      select=select, **kwargs))

    # ================================================================
    # Batch Analysis — SEO metrics for many targets in one call.
    # ================================================================

    @mcp.tool()
    def ahrefs_batch_analysis(
        targets: List[Dict[str, str]],
        select: List[str],
        country: Optional[str] = None,
        volume_mode: Optional[str] = None,
        order_by: Optional[List[str]] = None,
    ) -> object:
        """SEO metrics for up to many targets in ONE call — cheaper than N calls
        to `ahrefs_site_explorer` when comparing a batch of domains/URLs.

        Args:
            targets: list of {"url": ..., "mode": "exact"|"prefix"|"domain"|"subdomains",
                "protocol": "both"|"http"|"https"} — all 3 keys required per target.
            select: field names to return per target (see Site Explorer field
                names — Batch Analysis draws from the same metric surface).
            country: ISO 3166-1 alpha-2 country code.
            volume_mode: 'monthly'|'average'.
            order_by: sort spec, e.g. ["field_name:desc"].
        """
        if not targets or not all({"url", "mode", "protocol"} <= t.keys() for t in targets):
            raise _bad("`targets` doit être une liste de {'url','mode','protocol'}.")
        body: dict = {}
        if country is not None:
            body["country"] = country
        if volume_mode is not None:
            body["volume_mode"] = volume_mode
        if order_by is not None:
            body["order_by"] = order_by
        return _run(lambda: _client().batch_analysis(targets=targets, select=select, **body))

    # ================================================================
    # Subscription Info — account-level usage. Also the connection probe.
    # ================================================================

    @mcp.tool()
    def ahrefs_account() -> object:
        """Current Ahrefs API-unit consumption + subscription limits. Free, no args."""
        return _run(lambda: _client().subscription_info_limits_and_usage())

    # ================================================================
    # Management — Rank Tracker projects/keywords/competitors, keyword lists,
    # Brand Radar reports/prompts, location lookups. Reads + creates only —
    # see module docstring for why delete/patch stay client-only.
    # ================================================================

    @mcp.tool()
    def ahrefs_project(
        op: Literal["list", "create"] = "list",
        project_id: Optional[int] = None,
        access: Optional[str] = None,
        owned_by: Optional[str] = None,
        has_keywords: Optional[bool] = None,
        project_name: Optional[str] = None,
        url: Optional[str] = None,
        mode: Optional[str] = None,
        protocol: Optional[str] = None,
        folder_id: Optional[int] = None,
    ) -> object:
        """A Rank Tracker project — the list reachable with this key, or create one.

        Args:
            op: "list" (default, free) | "create".
            project_id: op="list" — filter to one project id.
            access: op="list" filter ('private'|'shared') | op="create" setting.
            owned_by: op="list" filter (owner email) | op="create" setting.
            has_keywords: op="list" — filter to projects with tracked keywords.
            project_name: REQUIRED by op="create" — the project's display name.
            url: REQUIRED by op="create" — the tracked target URL.
            mode: REQUIRED by op="create" — 'exact'|'prefix'|'domain'|'subdomains'.
            protocol: REQUIRED by op="create" — 'both'|'http'|'https'.
            folder_id: op="create" — folder to file the project under.
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.list_projects(**{
                k: v for k, v in dict(access=access, owned_by=owned_by, project_id=project_id,
                                       has_keywords=has_keywords).items() if v is not None}))
        if op == "create":
            for name, v in (("project_name", project_name), ("url", url), ("mode", mode),
                             ("protocol", protocol)):
                if v is None:
                    raise _bad(f"op='create' requiert {name}")
            body = {k: v for k, v in dict(access=access, owned_by=owned_by,
                                           folder_id=folder_id).items() if v is not None}
            return _run(lambda: client.create_project(project_name, url, mode, protocol, **body))
        raise _bad("op doit être 'list' ou 'create'")

    @mcp.tool()
    def ahrefs_project_keywords(
        project_id: int,
        op: Literal["list", "add", "tag"] = "list",
        keywords: Optional[List[Dict[str, Any]]] = None,
        locations: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        update_mode: Optional[str] = None,
    ) -> object:
        """Keywords tracked in a Rank Tracker project — list, add, or tag them.

        Args:
            project_id: the Rank Tracker project — required by every op.
            op: "list" (default, free) | "add" | "tag".
            keywords: REQUIRED by "add"/"tag". Shape differs by op (verified against
                Ahrefs' OpenAPI spec, 2026-08-20): "add" wants [{"keyword", "tags"?}]
                — the location goes in the PARALLEL `locations` list below, matched by
                position; "tag" wants [{"keyword", "country"?, "location_id"?,
                "language"?}] — location embedded per-item there instead.
            locations: REQUIRED by "add", same length/order as `keywords` — list of
                {"country", "location_id"?, "language"?} (`country` required per entry;
                values from `ahrefs_locations`). Not used by "tag".
            tags: REQUIRED by "tag" — tag strings to apply.
            update_mode: op="tag" — 'add' (default) | 'replace'.
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.project_keywords(project_id=project_id))
        if op == "add":
            if not keywords or not locations:
                raise _bad("op='add' requiert `keywords` et `locations` (même longueur, "
                           "appariés par position)")
            return _run(lambda: client.add_project_keywords(project_id, keywords, locations))
        if op == "tag":
            if not keywords or not tags:
                raise _bad("op='tag' requiert `keywords` et `tags`")
            kwargs = {"update_mode": update_mode} if update_mode is not None else {}
            return _run(lambda: client.tag_project_keywords(project_id, keywords, tags, **kwargs))
        raise _bad("op doit être 'list', 'add' ou 'tag'")

    @mcp.tool()
    def ahrefs_project_competitors(
        project_id: int,
        op: Literal["list", "add"] = "list",
        competitors: Optional[List[Dict[str, str]]] = None,
    ) -> object:
        """Competitors tracked on a Rank Tracker project — list or add them.

        Args:
            project_id: the Rank Tracker project.
            op: "list" (default, free) | "add".
            competitors: REQUIRED by "add" — list of {"url", "mode"}.
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.project_competitors(project_id=project_id))
        if op == "add":
            if not competitors:
                raise _bad("op='add' requiert `competitors`")
            return _run(lambda: client.add_project_competitors(project_id, competitors))
        raise _bad("op doit être 'list' ou 'add'")

    @mcp.tool()
    def ahrefs_locations(country_code: str, us_state: Optional[str] = None) -> object:
        """Location IDs + language codes for a country — feeds `location_id`/
        `language_code` on `ahrefs_rank_tracker(report="serp-overview")` and
        `country`/`location_id`/`language` in `ahrefs_project_keywords`.

        Args:
            country_code: ISO 3166-1 alpha-2 country code.
            us_state: ISO 3166-2:US state code — required only when country_code='us'.
        """
        kwargs = {"us_state": us_state} if us_state is not None else {}
        return _run(lambda: _client().locations(country_code=country_code, **kwargs))

    @mcp.tool()
    def ahrefs_keyword_list(
        keyword_list_id: int,
        op: Literal["list", "add"] = "list",
        keywords: Optional[List[str]] = None,
    ) -> object:
        """An Ahrefs keyword list — its keywords, or add more.

        Args:
            keyword_list_id: the keyword list id.
            op: "list" (default, free) | "add".
            keywords: REQUIRED by "add" — keyword strings to add.
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.keyword_list_keywords(keyword_list_id=keyword_list_id))
        if op == "add":
            if not keywords:
                raise _bad("op='add' requiert `keywords`")
            return _run(lambda: client.add_keyword_list_keywords(keyword_list_id, keywords))
        raise _bad("op doit être 'list' ou 'add'")

    @mcp.tool()
    def ahrefs_brand_radar_report(
        op: Literal["list", "create"] = "list",
        prompts_frequency: Optional[List[Dict[str, str]]] = None,
        project_id: Optional[int] = None,
        name: Optional[str] = None,
        brand: Optional[dict] = None,
        competitors: Optional[dict] = None,
    ) -> object:
        """A Brand Radar report (tracks brand/competitor visibility across AI
        chatbots) — list existing reports, or create one.

        Args:
            op: "list" (default, free) | "create".
            prompts_frequency: REQUIRED by "create" — [{"data_source": ...,
                "frequency": ...}, ...], one entry per AI platform to monitor.
                `data_source` values: 'chatgpt'|'gemini'|'perplexity'|'copilot'|
                'claude'|'grok'|'google_ai_overviews'|'google_ai_mode'. `frequency`
                values: 'daily'|'weekly'|'monthly'|'off'. Verified against Ahrefs'
                OpenAPI spec (2026-08-20): there is no separate top-level
                `data_source`/`frequency` field — this list is the only place they live.
            project_id: op="create" — link to a Rank Tracker project.
            name: op="create" — display name.
            brand: op="create" — {"names"?: [...], "url_groups"?: [...]}.
            competitors: op="create" — {"names"?: [...], "url_groups"?: [...]}.
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.brand_radar_reports())
        if op == "create":
            if not prompts_frequency:
                raise _bad("op='create' requiert `prompts_frequency`")
            body = {k: v for k, v in dict(project_id=project_id, name=name, brand=brand,
                                           competitors=competitors).items() if v is not None}
            return _run(lambda: client.create_brand_radar_report(prompts_frequency, **body))
        raise _bad("op doit être 'list' ou 'create'")

    @mcp.tool()
    def ahrefs_brand_radar_prompt(
        report_id: str,
        op: Literal["list", "create"] = "list",
        countries: Optional[List[str]] = None,
        prompts: Optional[List[str]] = None,
    ) -> object:
        """Custom prompts configured on a Brand Radar report — list or add them.

        Args:
            report_id: the Brand Radar report id (from its Ahrefs URL).
            op: "list" (default, free) | "create".
            countries: REQUIRED by "create" — ISO 3166-1 alpha-2 codes.
            prompts: REQUIRED by "create" — custom prompt strings (max 400 chars each).
        """
        client = _client()
        if op == "list":
            return _run(lambda: client.brand_radar_prompts(report_id=report_id))
        if op == "create":
            if not countries or not prompts:
                raise _bad("op='create' requiert `countries` et `prompts`")
            return _run(lambda: client.create_brand_radar_prompts(report_id, countries, prompts))
        raise _bad("op doit être 'list' ou 'create'")

    # ================================================================
    # Brand Radar (data) — AI-chatbot visibility of a brand/competitors.
    # ================================================================

    @mcp.tool()
    def ahrefs_brand_radar(
        report: Literal["ai-responses", "cited-pages", "cited-domains", "impressions-overview",
                         "citations-overview", "mentions-overview", "sov-overview",
                         "impressions-history", "citations-history", "mentions-history",
                         "sov-history"],
        data_source: str,
        select: Optional[str] = None,
        brand: Optional[str] = None,
        competitors: Optional[str] = None,
        country: Optional[str] = None,
        date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        where: Optional[str] = None,
        report_id: Optional[str] = None,
        prompts: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Brand/competitor visibility across AI chatbot responses. Custom-only
        prompts (`prompts="custom"`, needs `report_id`) are free; Ahrefs' own
        prompt data follows standard API-unit pricing.

        - **ai-responses**/**cited-pages**/**cited-domains**/**impressions-overview**/
          **mentions-overview**: current snapshot (need `select`, except sov-overview).
        - **sov-overview**/**sov-history**: share of voice (no `select`).
        - **citations-overview**/**citations-history**: citation counts (POST-only
          upstream — the request body shape genuinely differs from every other report
          in this tool, see below).
        - **\\*-history**: trend over `date_from`..`date_to`.

        Args:
            report: which Brand Radar report to run.
            data_source: comma-separated AI platforms, e.g. "chatgpt,gemini,perplexity"
                (also: copilot, claude, grok, google_ai_overviews, google_ai_mode).
            select: comma-separated columns — required except sov-overview/sov-history.
            brand/competitors: comma-separated brand/competitor names. At least one of
                brand/competitors/`extra["market"]`/where is required by most reports.
                **REFUSED on citations-overview/citations-history** — Ahrefs' POST body
                for those two has no `brand` field at all, and wants `competitors` as
                `[{"names": [...], "url_groups": [...]}]` objects, not a CSV string; use
                `extra={"brands": [...], "competitors": [...]}` there instead.
            country: comma-separated ISO 3166-1 alpha-2 codes (auto-converted to the
                list Ahrefs' POST body wants, on citations-overview/citations-history).
            date: snapshot date, YYYY-MM-DD (non-history reports). **REFUSED on
                citations-overview/citations-history** — neither takes a plain `date`.
            date_from/date_to: history window — required by *-history reports.
            where: Ahrefs filter-syntax expression (a string). **REFUSED on
                citations-overview/citations-history**, whose POST body wants `where`
                as a filter OBJECT, not a string — pass it via `extra` instead.
            report_id: scope to a pre-configured Brand Radar report.
            prompts: 'ahrefs'|'custom' (custom requires report_id).
            extra: `search_volume_type`, `tracked_urls` (cited-pages), and — REQUIRED
                path for citations-overview/citations-history filters —
                `brands`/`competitors` as `[{"names": [...], "url_groups": [...]}]`
                and `where` as a filter object. Merged last, overrides the typed args.
        """
        supplied = dict(data_source=data_source, select=select, brand=brand,
                         competitors=competitors, country=country, date=date,
                         date_from=date_from, date_to=date_to, where=where,
                         report_id=report_id, prompts=prompts)
        client = _client()
        if report in _BRAND_RADAR_POST_REPORTS:
            method_name, required = _BRAND_RADAR_POST_REPORTS[report]
            # Vérifié contre le spec OpenAPI d'Ahrefs (docs.ahrefs.com/openapi.json,
            # 2026-08-20) : le corps POST de citations-overview/citations-history
            # attend `country` en LISTE (comme data_source/select — même conversion),
            # mais `brand` (singulier) N'EXISTE PAS côté POST (seul `brands`, un
            # tableau d'objets {names,url_groups}, existe) et `competitors`/`where`
            # y sont aussi des LISTES/OBJETS, pas les chaînes CSV/texte du GET — les
            # transmettre tels quels serait un champ ignoré ou une 400 confuse, donc
            # REFUSÉS ici plutôt que silencieusement mal formés ; `extra` est la
            # voie correcte pour ces formes (déjà documentée dans le docstring).
            for name in ("brand", "competitors", "where", "date"):
                if supplied.get(name) is not None:
                    raise _bad(
                        f"report={report!r} (POST) n'accepte pas `{name}` sous cette forme "
                        f"(ou pas du tout — `date` n'existe pas côté POST, seul "
                        "`date_from`/`date_to` sur citations-history) — passe la forme "
                        "attendue via `extra` (ex. `extra={'brands': [{'names': [...]}]}`, "
                        "voir docs.ahrefs.com).")
            kwargs = {k: v for k, v in supplied.items()
                      if v is not None and k not in ("brand", "competitors", "where", "date")}
            # Le POST attend `data_source`/`select`/`country` en LISTE, le tool en CSV.
            for csv_field in ("data_source", "select", "country"):
                if csv_field in kwargs:
                    kwargs[csv_field] = kwargs[csv_field].split(",")
            missing = [r for r in required if r not in kwargs]
            if missing:
                raise _bad(f"report={report!r} requiert {', '.join(missing)}")
            if extra:
                kwargs.update(extra)
            return _run(lambda: getattr(client, method_name)(**kwargs))
        return _run(lambda: _call_report(client, report, _BRAND_RADAR_GET_REPORTS, supplied, extra))

    # ================================================================
    # Web Analytics — Ahrefs' own on-site analytics (needs the Ahrefs JS
    # snippet installed on the tracked site).
    # ================================================================

    @mcp.tool()
    def ahrefs_web_analytics(
        report: Literal[
            "stats", "chart", "source-channels", "source-channels-chart", "sources",
            "sources-chart", "referrers", "referrers-chart", "utm-params", "utm-params-chart",
            "entry-pages", "entry-pages-chart", "exit-pages", "exit-pages-chart", "top-pages",
            "top-pages-chart", "cities", "cities-chart", "continents", "continents-chart",
            "countries", "countries-chart", "languages", "languages-chart", "browsers",
            "browsers-chart", "browser-versions", "browser-versions-chart", "devices",
            "devices-chart", "operating-systems", "operating-systems-chart",
            "operating-systems-versions", "operating-systems-versions-chart",
        ],
        project_id: int,
        granularity: Optional[Literal["hourly", "daily", "weekly", "monthly"]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        where: Optional[str] = None,
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """On-site analytics for a Web Analytics project (visitors, sessions,
        breakdown by source/UTM/page/geo/device/browser/OS). Free. Distinct from
        Site Explorer's crawl-based traffic ESTIMATES — this is real visitor data,
        and needs the Ahrefs JS snippet installed on the tracked site.

        Args:
            report: 'stats'/'chart' = aggregate; every other value is a dimension
                breakdown, each with a plain and a `-chart` (time-series) variant.
            project_id: the Web Analytics project id (distinct from a Rank Tracker
                project id, despite the shared name).
            granularity: REQUIRED for any `*-chart` report.
            date_from/date_to: ISO 8601, bound the query window.
            where: Ahrefs filter-syntax expression.
            limit/order_by: row cap / sort — non-chart reports only.
            extra: that report's chart-series filter, under whatever name Ahrefs'
                docs give it for THIS specific report (e.g. `sources_to_chart` on
                sources-chart, `source_channels_to_chart` on source-channels-chart
                — the name genuinely differs per report upstream), merged last.
        """
        params: Dict[str, Any] = {}
        if granularity is not None:
            params["granularity"] = granularity
        if date_from is not None:
            params["from"] = date_from
        if date_to is not None:
            params["to"] = date_to
        if where is not None:
            params["where"] = where
        if limit is not None:
            params["limit"] = limit
        if order_by is not None:
            params["order_by"] = order_by
        if extra:
            params.update(extra)
        return _run(lambda: _client().web_analytics(report, project_id=project_id, **params))

    # ================================================================
    # GSC Insights — Google Search Console data via Ahrefs (project needs GSC
    # connected in the Ahrefs app).
    # ================================================================

    @mcp.tool()
    def ahrefs_gsc(
        report: Literal[
            "performance-history", "positions-history", "pages-history",
            "performance-by-device", "metrics-by-country", "ctr-by-position",
            "performance-by-position", "keyword-history", "keywords", "page-history",
            "pages", "anonymous-queries",
        ],
        date_from: str,
        project_id: Optional[int] = None,
        portfolio_id: Optional[int] = None,
        select: Optional[str] = None,
        country: Optional[str] = None,
        date_to: Optional[str] = None,
        device: Optional[str] = None,
        search_type: Optional[str] = None,
        where: Optional[str] = None,
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Google Search Console performance data, proxied through Ahrefs (the
        project/portfolio must have GSC connected in the Ahrefs app). Free.

        Args:
            report: which GSC report to run. `anonymous-queries` has a different
                shape (needs `select`+`country`, no `portfolio_id`) — every other
                value needs one of `project_id`/`portfolio_id`.
            date_from: period start, YYYY-MM-DD.
            project_id: a single GSC-connected project.
            portfolio_id: aggregate across a portfolio's GSC-connected projects
                (not accepted by anonymous-queries).
            select: REQUIRED by anonymous-queries only.
            country: ISO 3166-1 alpha-2 — REQUIRED by anonymous-queries, optional filter elsewhere.
            date_to: period end, YYYY-MM-DD (omit = open-ended).
            device: 'desktop'|'mobile'|'tablet'.
            search_type: 'web'(default)|'image'|'video'|'news'.
            where: filter expression (JSON boolean, supports `url` on most reports).
            limit/order_by: row cap / sort — keywords/pages/anonymous-queries only.
            extra: `history_grouping` ('daily'|'weekly'|'monthly', default monthly),
                `keywords` (keyword-history), `keyword_list_id`/`keyword_lists`
                (keywords), `pages` (page-history), `timeout`, merged last.
        """
        if report == "anonymous-queries":
            if select is None or country is None:
                raise _bad("report='anonymous-queries' requiert `select` et `country`")
            if project_id is None:
                raise _bad("report='anonymous-queries' requiert `project_id` "
                            "(pas de `portfolio_id` sur ce rapport)")
            kwargs = {k: v for k, v in dict(limit=limit, order_by=order_by, where=where).items()
                      if v is not None}
            if extra:
                kwargs.update(extra)
            return _run(lambda: _client().gsc_anonymous_queries(
                select=select, project_id=project_id, date_from=date_from, country=country, **kwargs))
        if project_id is None and portfolio_id is None:
            raise _bad(f"report={report!r} requiert `project_id` ou `portfolio_id`")
        kwargs = {k: v for k, v in dict(
            project_id=project_id, portfolio_id=portfolio_id, date_to=date_to, country=country,
            device=device, search_type=search_type, where=where).items() if v is not None}
        if extra:
            kwargs.update(extra)
        return _run(lambda: _client().gsc_report(report, date_from=date_from, **kwargs))

    # ================================================================
    # Social Media — connected channels, posts, engagement + publishing.
    # ================================================================

    @mcp.tool()
    def ahrefs_social(
        op: Literal["channels", "channel_metrics", "authors", "activity_history",
                     "posts", "post_metrics", "publish"] = "channels",
        channel_id: Optional[str] = None,
        post_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[Literal["published", "scheduled", "draft", "failed", "deleted"]] = None,
        external_post_id: Optional[str] = None,
        channel_ids: Optional[List[str]] = None,
        text_content: Optional[str] = None,
        timing: Optional[Literal["publish_now", "scheduled", "draft"]] = None,
        scheduled_at: Optional[str] = None,
        auto_comment: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> object:
        """Ahrefs Social Media — connected channels, posts, engagement, and
        publishing. Editing/deleting an existing post stays out of this tool
        (client-only) — publishing a NEW one is the one write exposed here.

        Args:
            op: "channels" (default, connected channels) | "channel_metrics"
                (follower history) | "authors" | "activity_history" (a post's
                change log) | "posts" (list by status) | "post_metrics"
                (engagement of one published post) | "publish" (send a new post).
            channel_id: required by "channel_metrics".
            post_id: required by "activity_history".
            date_from: required by "channel_metrics"/"post_metrics".
            date_to: optional end of range on the same two ops.
            status: required by "posts".
            external_post_id: required by "post_metrics" (with channel_id).
            channel_ids: required by "publish" — destination channel ids.
            text_content: required by "publish" — the message body.
            timing: required by "publish" — 'publish_now'|'scheduled'|'draft'.
            scheduled_at: required by "publish" when timing='scheduled' (ISO 8601).
            auto_comment: "publish" — a follow-up comment posted after publishing.
            extra: op="posts" only — `channel_ids`, `author_ids`, `search_query`,
                `order_by`, `order_direction`, `limit`, `offset`, merged last.
        """
        client = _client()
        if op == "channels":
            return _run(lambda: client.social_channels())
        if op == "channel_metrics":
            if not channel_id or not date_from:
                raise _bad("op='channel_metrics' requiert `channel_id` et `date_from`")
            kwargs = {"date_to": date_to} if date_to is not None else {}
            return _run(lambda: client.social_channel_metrics(channel_id, date_from, **kwargs))
        if op == "authors":
            return _run(lambda: client.social_authors())
        if op == "activity_history":
            if post_id is None:
                raise _bad("op='activity_history' requiert `post_id`")
            return _run(lambda: client.social_activity_history(post_id))
        if op == "posts":
            if not status:
                raise _bad("op='posts' requiert `status`")
            kwargs = {k: v for k, v in dict(date_from=date_from, date_to=date_to).items()
                      if v is not None}
            if extra:
                kwargs.update(extra)
            return _run(lambda: client.social_posts(status, **kwargs))
        if op == "post_metrics":
            if not external_post_id or not channel_id or not date_from:
                raise _bad("op='post_metrics' requiert `external_post_id`, `channel_id`, `date_from`")
            kwargs = {"date_to": date_to} if date_to is not None else {}
            return _run(lambda: client.social_post_metrics(
                external_post_id, channel_id, date_from, **kwargs))
        if op == "publish":
            if not channel_ids or not text_content or not timing:
                raise _bad("op='publish' requiert `channel_ids`, `text_content`, `timing`")
            if timing == "scheduled" and not scheduled_at:
                raise _bad("timing='scheduled' requiert `scheduled_at`")
            body = {k: v for k, v in dict(scheduled_at=scheduled_at,
                                           auto_comment=auto_comment).items() if v is not None}
            return _run(lambda: client.create_social_post(channel_ids, text_content, timing, **body))
        raise _bad("op inconnu")

    # ================================================================
    # Public — no-auth crawler IPs + free-tier Domain Rating.
    # ================================================================

    @mcp.tool()
    def ahrefs_public(
        op: Literal["crawler_ips", "crawler_ip_ranges", "domain_rating_free",
                     "domain_rating_top_domains"] = "domain_rating_free",
        target: Optional[str] = None,
        rank_from: Optional[int] = None,
        rank_to: Optional[int] = None,
    ) -> object:
        """Ahrefs' free/public endpoints — crawler IP allowlisting + free Domain Rating.

        Args:
            op: "crawler_ips"/"crawler_ip_ranges" (Ahrefs bot's addresses, no key
                needed at all) | "domain_rating_free" (default — Domain Rating for
                `target`; usage requires crediting "Domain Rating by Ahrefs") |
                "domain_rating_top_domains" (top domains by Domain Rating).
            target: required by "domain_rating_free".
            rank_from/rank_to: "domain_rating_top_domains" — rank window (default
                1..100, max 250k rows/request).
        """
        client = _client()
        if op == "crawler_ips":
            return _run(lambda: client.crawler_ips())
        if op == "crawler_ip_ranges":
            return _run(lambda: client.crawler_ip_ranges())
        if op == "domain_rating_free":
            if not target:
                raise _bad("op='domain_rating_free' requiert `target`")
            return _run(lambda: client.domain_rating_free(target))
        if op == "domain_rating_top_domains":
            kwargs = {}
            if rank_from is not None:
                kwargs["from"] = rank_from
            if rank_to is not None:
                kwargs["to"] = rank_to
            return _run(lambda: client.domain_rating_top_domains(**kwargs))
        raise _bad("op inconnu")
