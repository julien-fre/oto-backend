## prerequisite — clé api firecrawl

crée une clé API dans [Firecrawl](https://www.firecrawl.dev/app/api-keys) (elle commence par `fc-`), puis colle-la dans oto.
- byo-only : pas de clé oto partagée, chaque compte/org paie ses crédits

## usage — lire des pages web en markdown propre

récupère le contenu d'une page (ou d'un site entier) tel qu'un humain le voit : le JavaScript est exécuté, la nav et les pubs sont retirées, il reste du markdown exploitable.
- « résume cette page » / « extrais les tarifs de cette URL » → `firecrawl_scrape`
- « quelles pages a ce site ? » → `firecrawl_map` (les URLs seules, rapide et peu cher — à faire AVANT un crawl)
- « aspire tout le blog de ce site » → `firecrawl_crawl` puis `firecrawl_crawl_status` (asynchrone : le job rend un id, on relit jusqu'à `completed`)
- « cherche X sur le web et donne-moi le contenu des résultats » → `firecrawl_search` avec `scrape_options={"formats": ["markdown"]}`
- « extrais le même tableau de champs sur ces 30 pages » → `firecrawl_extract` (donne un `schema`, pas seulement un prompt) puis `firecrawl_extract_status`

## note — coût et choix de l'outil

chaque page rendue consomme des crédits : poser `limit` sur un crawl est la seule protection (défaut API : 10 000 pages).
- page stable → `max_age` accepte une version en cache, bien moins chère qu'un rendu neuf
- une seule page à extraire en structuré → `firecrawl_scrape` avec `formats=[{"type": "json", "schema": {...}}]` : synchrone, un seul appel, moins cher qu'`extract`
- site qui bloque → `proxy="stealth"` (plus cher, à ne pas mettre par défaut)
- cookie wall / bouton « voir plus » → `actions` (click, wait, scroll) avant capture
- juste le HTML brut d'une URL, sans rendu JS ? `serper_scrape` suffit. page derrière un login ? c'est le connecteur `browser`.
