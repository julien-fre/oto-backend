## prerequisite — ta clé api promptwatch

promptwatch expose une clé api par projet (ou par organisation). va dans **settings → api keys** sur le [dashboard promptwatch](https://promptwatch.com), et crée une clé.
- colle-la dans oto sur ton compte (`/account`), connecteur **promptwatch**
- byo uniquement : ta clé ou celle partagée de ton org, pas de clé plateforme (pas d'accord commercial otomata↔promptwatch)
- si ta clé est **org-level** (couvre plusieurs projets), renseigne aussi le **project id** — utilise `promptwatch_project` pour lister les projets accessibles et récupérer son id. une clé **project-level** est déjà scopée, laisse ce champ vide.

## usage — visibilité ia de ta marque

suit comment ta marque/produit apparaît dans les réponses de chatgpt, claude, gemini… sur un ensemble de prompts organisés en monitors, avec analytics de visibilité/sentiment/citations et génération de contenu pour combler les manques.
- « crée un monitor pour suivre "crm alternatives" » → `promptwatch_monitor` (op `create`), puis `promptwatch_prompt` (op `create` ou `bulk_create`) pour y attacher des prompts
- « comment évolue notre visibilité ce mois-ci » → `promptwatch_visibility` (op `time_series`), et `op="competitor_heatmap"` pour se comparer aux concurrents
- « que disent les réponses ia sur nous, positif ou négatif » → `promptwatch_response` (op `sentiment_distribution` ou `sentiment_time_series`)
- « quels sites sont cités le plus souvent » → `promptwatch_citation` (op `top_pages`, `domains_over_time`, `llm_sources`…)
- « quels prompts on ne couvre pas encore, et propose du contenu » → `promptwatch_content` (op `gap_prompts`, `gap_recommendations`, puis `create` en mode CREATE ou OPTIMIZE)
- « publie ce contenu sur notre cms » → `promptwatch_publishing` (op `push_draft` puis `publish_live`, après avoir listé les connexions cms avec `op="list_connections"`)
- « où en sont les créneaux de contenu planifiés par l'agent » → `promptwatch_content_agent` (op `list_slots`, `accept_slot`, `publish_slot_now`)
- « des pubs concurrentes apparaissent dans les réponses ia ? » → `promptwatch_ads`, et pour l'e-commerce (position des produits, top marchands) → `promptwatch_shopping`
- « organise mes prompts par tag ou par thème » → `promptwatch_taxonomy` (tags/topics), `promptwatch_persona` (angle d'audience) et `promptwatch_brand` (concurrents suivis)
- « suivi de pages précises (les nôtres ou d'un concurrent) citées par l'ia » → `promptwatch_page_tracker`, distinct du crawl de site (`promptwatch_sitemap`, santé seo incluse)
