## prerequisite — tavily api key

create an API key in [Tavily](https://app.tavily.com/home) (it starts with `tvly-`), then paste it into oto. the free tier gives 1,000 credits per month.
- an oto platform key can also be set by the super-admin: it serves as a fallback when neither the account nor the org has its own

## usage — search the web and read pages, tailored for an agent

a search returns cited excerpts AND a synthesized answer; a read returns the clean markdown of several URLs at once.
- "what is X?" / "find me recent info on Y" → `tavily_search` (set `topic="news"` and `time_range` for current events)
- "read these 5 pages and summarize" → `tavily_extract` with the list of URLs (failed URLs come back in `failed_results`, the rest goes through)
- "what pages does this site have?" → `tavily_map` (URLs only, do it BEFORE a crawl)
- "get the docs / case studies of this site" → `tavily_crawl` with `instructions` in natural language (synchronous, 100 pages max)

## note — cost and choice of tool

each response carries `usage.credits`: search 1 credit (`advanced` 2), extract 1 credit per 5 URLs, crawl/map 1 credit per 10 pages (×2 with `instructions` or `advanced`).
- raw Google SERP (rankings, People Also Ask) → `serper_search`, not Tavily
- ONE page with JavaScript executed, or a page that blocks → `firecrawl_scrape`
- crawl of a whole domain → `firecrawl_crawl` (asynchronous, uncapped); `tavily_crawl` is bounded to 100 pages / 40 s
- `include_raw_content` on a search makes the response much heavier: prefer `tavily_extract` on the URLs you keep

## note — project perimeter (#605, 2026-08-29)

under a project with `excluded_url_prefixes`, `tavily_search`, `tavily_map` and `tavily_crawl` drop the matching results/pages and say so (`excluded_by_perimeter`); `tavily_extract`, `tavily_map` and `tavily_crawl` **refuse** a matching URL (for `extract`, the whole batch is refused, naming the URLs). the synthesized `answer` is prose: it is not filtered. details: `docs/projects.md`.
