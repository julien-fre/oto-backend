## prerequisite — clé api cloro

crée une clé API dans [Cloro](https://cloro.dev), puis colle-la dans oto.
- les members consomment un quota plateforme si aucune clé perso/org n'est posée

## usage — veille ai-search + serp google en json

interroge les moteurs IA (ChatGPT, Gemini, Perplexity, Copilot, Grok, Google AI Mode) et capture leurs réponses + sources — veille de marque « AI SEO » — plus la SERP/News Google en JSON propre. (les appels moteurs IA prennent ~30-45 s.)
- « que dit ChatGPT de la marque X ? » (réponse + citations)
- « compare ce que disent Gemini et Perplexity sur ce produit »
- « SERP Google de `meilleur CRM` avec l'AI Overview »
- « Google News sur cette entreprise »

## note — périmètre de projet (#605, 2026-08-29)

sous un projet à `excluded_url_prefixes`, `cloro_google` (serp, news) et `cloro_ask` (sources/citations) écartent les résultats correspondants et le disent (`excluded_by_perimeter`). la réponse d'un moteur ia est de la prose : non filtrée. détail : `docs/projects.md`.
