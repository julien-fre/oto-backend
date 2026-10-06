## prerequisite — apify api token

get the API token from [Apify](https://console.apify.com/settings/integrations) (it starts with `apify_api_`), then paste it into oto.
- byo-only: runs are billed to the org's Apify account

## usage — run a ready-made scraper (the "actors")

apify is a catalog of ready-to-use scrapers — Google Maps, LinkedIn, Instagram, Amazon, Booking, TikTok… — that you launch with a JSON input and whose output you read.
- "scrape the Google Maps reviews of bakeries in Marseille" → `apify_store_search("google maps")` to find the actor, then `apify_run_sync` with its input
- "which actor for this site?" → `apify_store_search` (also returns each actor's price and popularity)
- "what options does this actor have?" → `apify_actor` (default memory and timeout; the INPUT fields are documented on its Store page)
- long run (> 5 min) → `apify_run`, then `apify_run_status` until `SUCCEEDED`, finally `apify_dataset_items(defaultDatasetId)`
- "stop that" → `apify_abort_run` (stops billing)

## note — identifier, input and cost

- an actor is written `username/actor-name` (what the Store shows) or by its id — both work, the conversion to the URL form is done for you
- `run_input` is **specific to each actor**: its fields are not invented, they are read on the Store page (e.g. `{"searchStringsArray": [...], "maxCrawledPlaces": 20}` for the Google Maps scraper)
- a run is billed by usage: setting `max_items` (and if needed `max_total_charge_usd`, `timeout_secs`) at LAUNCH is the only protection — afterwards it is consumed
- `apify_run_sync` waits at most 300 s; beyond that Apify answers 408 and you must use the asynchronous mode
- an actor's items are often very wide: `fields` / `omit` avoid bringing back huge objects
