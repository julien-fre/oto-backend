## prerequisite — get a serpapi key

create an api key (`private api key`) in your [serpapi](https://serpapi.com) dashboard.
- paste it into your oto connectors on `/account`
- members can also use the platform key (daily quota)

## usage — multi-engine search

reaches engines that serper does not have: google verticals (trends, finance, flights, hotels, events, jobs), bing, youtube and marketplaces.
- `serpapi_search(engine=…)` — any serpapi engine: `bing`, `youtube`, `amazon`, `walmart`, `ebay`, `google_events`, and the generic one (google_play, duckduckgo, yelp…)
- `serpapi_jobs(op="search"|"details")` — job posting sourcing via google jobs (the `job_id` for the details comes out of the search)
- `serpapi_google_trends` — interest over time / by region for a term
- `serpapi_google_finance` / `serpapi_google_flights` / `serpapi_google_hotels` — quotes, flights, hotels

## note — project scope (#605, 2026-08-29)

under a project with `excluded_url_prefixes`, `serpapi_search` drops the matching results — whatever the engine — and says so (`excluded_by_perimeter`). verticals with their own contract (jobs, trends, finance, flights, hotels) do not return web pages and are not filtered. details: `docs/projects.md`.
