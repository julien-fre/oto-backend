## prerequisite — obtenir une clé serpapi

crée une clé api (`private api key`) dans ton dashboard [serpapi](https://serpapi.com).
- colle-la dans tes connecteurs oto sur `/account`
- les membres peuvent aussi utiliser la clé plateforme (quota quotidien)

## usage — recherche multi-moteurs

atteint des moteurs que serper n'a pas : verticaux google (trends, finance, vols, hôtels, events, jobs), bing, youtube et marketplaces.
- `serpapi_search(engine=…)` — n'importe quel moteur serpapi : `bing`, `youtube`, `amazon`, `walmart`, `ebay`, `google_events`, et le générique (google_play, duckduckgo, yelp…)
- `serpapi_jobs(op="search"|"details")` — sourcing d'offres via google jobs (le `job_id` du détail sort de la recherche)
- `serpapi_google_trends` — intérêt dans le temps / par région pour un terme
- `serpapi_google_finance` / `serpapi_google_flights` / `serpapi_google_hotels` — cotations, vols, hôtels
