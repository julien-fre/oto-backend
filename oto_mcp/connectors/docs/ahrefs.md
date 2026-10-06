## prerequisite — ahrefs api key

create an API key in Ahrefs (Account → API Access — see the [authentication doc](https://docs.ahrefs.com/en/api/docs/api-keys-creation-and-management)), then paste it into oto.
- byo-only: no shared oto key, each org uses its own Ahrefs subscription
- one Ahrefs seat covers several products (Site Explorer, Keywords Explorer, Site Audit, Rank Tracker, Brand Radar…) — a single key reaches them all

## usage — SEO, backlinks, keywords, rank tracking

ahrefs covers most of the Ahrefs v3 API, grouped by product:
- "what is this domain's link profile / organic position?" → `ahrefs_site_explorer(report="domain-rating"|"organic-keywords"|"all-backlinks"|..., target="example.com")`
- "what search volume / difficulty for these keywords?" → `ahrefs_keywords_explorer(report="overview", keywords="word1,word2", country="fr")`
- "what technical problems on this crawled site?" → `ahrefs_site_audit(report="issues", project_id=...)` (project_id from an existing Site Audit project on the Ahrefs side)
- "where does this keyword rank for my tracked projects?" → `ahrefs_rank_tracker(report="overview", project_id=..., date=..., device="desktop")` (`project_id` via `ahrefs_project(op="list")`)
- "SERP for any keyword, without a Rank Tracker project" → `ahrefs_serp_overview(keyword=..., country=...)`
- "compare N domains in one call" → `ahrefs_batch_analysis(targets=[...], select=[...])`
- "where do I stand on my Ahrefs quota?" → `ahrefs_account()` (free)
- "what is my brand visibility on ChatGPT/Gemini/Perplexity…?" → `ahrefs_brand_radar(report="mentions-overview", data_source="chatgpt,gemini", brand="MyBrand")`
- "on-site analytics (visitors, sources, geo, device)" → `ahrefs_web_analytics(report="stats"|"sources"|"countries"|..., project_id=...)` (requires the Ahrefs JS snippet installed on the tracked site)
- "Google Search Console data" → `ahrefs_gsc(report="keywords"|"pages"|..., date_from=..., project_id=...)` (requires GSC connected to the project on the Ahrefs side)
- "manage my projects/tracked keywords" → `ahrefs_project`, `ahrefs_project_keywords`, `ahrefs_project_competitors`, `ahrefs_keyword_list`, `ahrefs_locations`
- "publish on the connected social networks" → `ahrefs_social(op="publish", ...)`

## note — `select`, units, and what is NOT exposed for writing

- most Ahrefs reports require `select` (columns to return) — a default column set is applied for the most used reports (organic-keywords, top-pages, all-backlinks, refdomains, anchors, organic-competitors, pages-by-backlinks, keywords-explorer overview/matching-terms/related-terms, rank-tracker overview, serp-overview); elsewhere, `select` stays required as-is — see [the Ahrefs doc](https://docs.ahrefs.com) for the valid columns of the targeted report
- **this default `select` includes columns billed beyond the base cost** (costs checked against Ahrefs' OpenAPI spec, 2026-08-20): `volume`/`keyword_difficulty`/`sum_traffic` (10 units each) on organic-keywords; `sum_traffic`/`top_keyword_volume` (10 u.) on top-pages; `traffic_domain` (10 u.) on refdomains; `refdomains` (5 u.) on anchors; `traffic` (10 u.) on organic-competitors/serp-overview; `refdomains_target` (5 u.) on pages-by-backlinks; `volume`/`difficulty` (10 u. each) on the 3 default keywords-explorer reports — deliberate (they are the useful columns), but worth knowing before scaling `limit` on these reports
- most reports default to `limit=1000` rows on the Ahrefs side if omitted; `ahrefs_site_audit(report="issues"|"page-content")` costs 50 units/request whatever `limit` is — pass `limit` explicitly to bound the spend
- `extra` (dict) is the escape hatch to any Ahrefs parameter not typed above (merged last, takes precedence over the typed args)
- deleting/modifying existing resources (Rank Tracker project, tracked keywords, competitors, Brand Radar report) is **not** exposed — read and create only, by choice (same stance as Silae: a deletion is a deliberate act, never a side effect)
- **verification**: parameters, request bodies and `select` columns are checked word for word against Ahrefs' OpenAPI spec (`docs.ahrefs.com/openapi.json`, 2026-08-20) — not against a doc page summary. However, no call was made to the real API (no key available during the build): the spec says what Ahrefs documents as accepted, not what it actually accepts in prod