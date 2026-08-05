## prerequisite — token api apify

récupère le token API dans [Apify](https://console.apify.com/settings/integrations) (il commence par `apify_api_`), puis colle-le dans oto.
- byo-only : les runs sont facturés sur le compte Apify de l'org

## usage — lancer un scraper déjà écrit (les « actors »)

apify est un catalogue de scrapers prêts à l'emploi — Google Maps, LinkedIn, Instagram, Amazon, Booking, TikTok… — qu'on lance avec un JSON d'entrée et dont on lit la sortie.
- « scrape les avis Google Maps des boulangeries de Marseille » → `apify_store_search("google maps")` pour trouver l'actor, puis `apify_run_sync` avec son input
- « quel actor pour ce site ? » → `apify_store_search` (rend aussi le prix et la popularité de chacun)
- « quelles options a cet actor ? » → `apify_actor` (mémoire et timeout par défaut ; les champs d'INPUT sont documentés sur sa fiche du Store)
- run long (> 5 min) → `apify_run` puis `apify_run_status` jusqu'à `SUCCEEDED`, enfin `apify_dataset_items(defaultDatasetId)`
- « arrête ça » → `apify_abort_run` (stoppe la facturation)

## note — l'identifiant, l'input et le coût

- un actor s'écrit `username/actor-name` (ce que montre le Store) ou par son id — les deux marchent, la conversion vers la forme d'URL est faite pour toi
- `run_input` est **propre à chaque actor** : ses champs ne s'inventent pas, ils se lisent sur la fiche du Store (ex. `{"searchStringsArray": [...], "maxCrawledPlaces": 20}` pour le scraper Google Maps)
- un run se facture à l'usage : poser `max_items` (et au besoin `max_total_charge_usd`, `timeout_secs`) au LANCEMENT est la seule protection — après, c'est consommé
- `apify_run_sync` attend au plus 300 s ; au-delà Apify répond 408 et il faut passer par le mode asynchrone
- les items d'un actor sont souvent très larges : `fields` / `omit` évitent de ramener des objets énormes
