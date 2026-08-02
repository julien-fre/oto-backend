## prerequisite — obtenir une clé serpapi

crée une clé api (`private api key`) dans ton dashboard [serpapi](https://serpapi.com).
- colle-la dans tes connecteurs oto sur `/account`
- les membres peuvent aussi utiliser la clé plateforme (quota quotidien)

## usage — recherche multi-moteurs

atteint des moteurs que serper n'a pas : verticaux google (trends, finance, vols, hôtels, events, jobs), bing, youtube et marketplaces.
- `serpapi_search` — appel générique vers n'importe quel moteur serpapi (google_play, duckduckgo, yelp…)
- `serpapi_search_jobs` + `serpapi_job_details` — sourcing d'offres via google jobs
- `serpapi_google_trends` — intérêt dans le temps / par région pour un terme
- `serpapi_youtube_search` / `serpapi_amazon_search` — vidéos, produits
