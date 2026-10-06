## prerequisite — get a serper key

create an api key on [serper.dev](https://serper.dev) (sign up, then the key is in your dashboard).
- paste it into your oto connectors at `/account`
- members can also use the shared platform key (daily quota); without an account, your own key is required

## usage — google search + scraping

query the whole google universe (web, news, images, videos, places, maps, reviews, shopping, scholar, patents, lens) and scrape a page.
- `serper_search(kind=…)` — one vertical per `kind`: `web` (filterable by site/country/date, e.g. profiles on `linkedin.com/in`), `news` (signal monitoring: fundraise, hiring, press), `places` (local b2b prospecting — title, address, phone, website, rating), `images`, `videos`, `shopping`, `scholar`, `patents`, `autocomplete`
- `serper_reviews` — the reviews of a place; returns **all** of them by default (`op="page"` for a simple sample)
- `serper_maps_sample` / `serper_maps_census` — a sample of places, or the **exhaustive census** of an area (tiles, paginates and deduplicates server-side)
- `serper_lens` — reverse search from an image
- `serper_scrape` — fetches the content of a page (markdown), handles js and light anti-bot

## note — project perimeter (#605, 2026-08-29)

under a project whose `excluded_url_prefixes` option is set (e.g. `linkedin.com/in/`), `serper_search` and `serper_lens` **drop** the matching results and say so (`excluded_by_perimeter`: how many, by which project, for which pattern); `serper_scrape` and `serper_lens` **refuse** a matching URL, naming the pattern and the project. without a project or without the option, nothing changes. the company page (`linkedin.com/company/`) is still rendered: the patterns are precise. details: `docs/projects.md`.
