## prerequisite — obtenir une clé serper

crée une clé api sur [serper.dev](https://serper.dev) (inscription, puis la clé est dans ton dashboard).
- colle-la dans tes connecteurs oto sur `/account`
- les membres peuvent aussi taper la clé plateforme partagée (quota quotidien) ; sans compte, ta propre clé est obligatoire

## usage — recherche google + scraping

interroge tout l'univers google (web, news, images, vidéos, lieux, maps, avis, shopping, scholar, brevets, lens) et scrape une page.
- `serper_search(kind=…)` — une verticale par `kind` : `web` (filtrable par site/pays/date, ex. profils sur `linkedin.com/in`), `news` (veille signaux : levée, recrutement, presse), `places` (prospection b2b locale — titre, adresse, téléphone, site, note), `images`, `videos`, `shopping`, `scholar`, `patents`, `autocomplete`
- `serper_reviews` — les avis d'un lieu ; rend **tout** par défaut (`op="page"` pour un simple échantillon)
- `serper_maps_sample` / `serper_maps_census` — un échantillon de lieux, ou le **recensement exhaustif** d'une zone (pave, pagine et déduplique côté serveur)
- `serper_lens` — recherche inversée à partir d'une image
- `serper_scrape` — récupère le contenu d'une page (markdown), gère le js et l'anti-bot léger

## note — périmètre de projet (#605, 2026-08-29)

sous un projet dont l'option `excluded_url_prefixes` est posée (ex. `linkedin.com/in/`), `serper_search` et `serper_lens` **écartent** les résultats correspondants et le disent (`excluded_by_perimeter` : combien, par quel projet, par quel motif) ; `serper_scrape` et `serper_lens` **refusent** une URL correspondante en nommant le motif et le projet. sans projet ou sans option, rien ne change. la page entreprise (`linkedin.com/company/`) reste rendue : les motifs sont précis. détail : `docs/projects.md`.
