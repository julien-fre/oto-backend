## prerequisite — firecrawl api key

create an API key in [Firecrawl](https://www.firecrawl.dev/app/api-keys) (it starts with `fc-`), then paste it into oto.
- byo-only: no shared oto key, each account/org pays for its own credits

## usage — read web pages as clean markdown

fetches the content of a page (or a whole site) as a human sees it: JavaScript is executed, nav and ads are stripped, and usable markdown remains.
- "summarize this page" / "extract the pricing from this URL" → `firecrawl_scrape`
- "what pages does this site have?" → `firecrawl_map` (URLs only, fast and cheap — do this BEFORE a crawl)
- "pull the whole blog of this site" → `firecrawl_crawl` then `firecrawl_crawl_status` (asynchronous: the job returns an id, poll until `completed`)
- "search the web for X and give me the content of the results" → `firecrawl_search` with `scrape_options={"formats": ["markdown"]}`
- "extract the same set of fields from these 30 pages" → `firecrawl_extract` (give a `schema`, not just a prompt) then `firecrawl_extract_status`

## note — cost and choosing the tool

every rendered page consumes credits: setting `limit` on a crawl is the only safeguard (API default: 10,000 pages).
- stable page → `max_age` accepts a cached version, much cheaper than a fresh render
- a single page to extract in structured form → `firecrawl_scrape` with `formats=[{"type": "json", "schema": {...}}]`: synchronous, a single call, cheaper than `extract`
- site that blocks → `proxy="stealth"` (more expensive, don't set it by default)
- cookie wall / "see more" button → `actions` (click, wait, scroll) before capture
- just the raw HTML of a URL, no JS rendering? `serper_scrape` is enough. page behind a login? that's the `browser` connector.

## note — project scope (#605, 2026-08-29)

under a project with `excluded_url_prefixes`, `firecrawl_search`, `firecrawl_map` and `firecrawl_crawl_status` drop the matching results/pages and say so (`excluded_by_perimeter`); `firecrawl_scrape`, `firecrawl_map`, `firecrawl_crawl` and `firecrawl_extract` **refuse** a matching URL (the whole batch is refused for `extract`). `firecrawl_extract_status` returns data in the caller's schema, not pages: not filtered. details: `docs/projects.md`.
