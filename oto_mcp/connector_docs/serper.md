## prerequisite — obtenir une clé serper

crée une clé api sur [serper.dev](https://serper.dev) (inscription, puis la clé est dans ton dashboard).
- colle-la dans tes connecteurs oto sur `/account`
- les membres peuvent aussi taper la clé plateforme partagée (quota quotidien) ; sans compte, ta propre clé est obligatoire

## usage — recherche google + scraping

interroge tout l'univers google (web, news, images, vidéos, lieux, maps, avis, shopping, scholar, brevets, lens) et scrape une page.
- `serper_web_search` — recherche web google, filtrable par site/pays/date (ex. profils sur `linkedin.com/in`)
- `serper_news_search` — veille signaux sur une cible (levée, recrutement, presse)
- `serper_places_search` — prospection b2b locale (titre, adresse, téléphone, site, note)
- `serper_scrape` — récupère le contenu d'une page (texte + markdown), gère le js et l'anti-bot léger
