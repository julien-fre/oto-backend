## prerequisite — clé api tavily

crée une clé API dans [Tavily](https://app.tavily.com/home) (elle commence par `tvly-`), puis colle-la dans oto. le palier gratuit donne 1 000 crédits par mois.
- une clé plateforme oto est aussi posable par le super-admin : elle sert de repli quand ni le compte ni l'org n'ont la leur

## usage — chercher sur le web et lire des pages, taillé pour un agent

une recherche rend des extraits cités ET une réponse synthétique ; une lecture rend le markdown propre de plusieurs URLs d'un coup.
- « qu'est-ce que X ? » / « trouve-moi des infos récentes sur Y » → `tavily_search` (mettre `topic="news"` et `time_range` pour l'actualité)
- « lis ces 5 pages et résume » → `tavily_extract` avec la liste d'URLs (les URLs en échec reviennent dans `failed_results`, le reste passe)
- « quelles pages a ce site ? » → `tavily_map` (URLs seules, à faire AVANT un crawl)
- « récupère la doc / les études de cas de ce site » → `tavily_crawl` avec `instructions` en langage naturel (synchrone, 100 pages max)

## note — coût et choix de l'outil

chaque réponse porte `usage.credits` : search 1 crédit (`advanced` 2), extract 1 crédit par 5 URLs, crawl/map 1 crédit par 10 pages (×2 avec `instructions` ou `advanced`).
- SERP Google brut (positions, People Also Ask) → `serper_search`, pas Tavily
- UNE page avec JavaScript exécuté, ou une page qui bloque → `firecrawl_scrape`
- crawl d'un domaine entier → `firecrawl_crawl` (asynchrone, sans plafond) ; `tavily_crawl` est borné à 100 pages / 40 s
- `include_raw_content` sur une recherche alourdit beaucoup la réponse : préférer `tavily_extract` sur les URLs retenues

## note — périmètre de projet (#605, 2026-08-29)

sous un projet à `excluded_url_prefixes`, `tavily_search`, `tavily_map` et `tavily_crawl` écartent les résultats/pages correspondants et le disent (`excluded_by_perimeter`) ; `tavily_extract`, `tavily_map` et `tavily_crawl` **refusent** une URL correspondante (pour `extract`, tout le lot est refusé en nommant les URLs). la réponse synthétique `answer` est de la prose : elle n'est pas filtrée. détail : `docs/projects.md`.
