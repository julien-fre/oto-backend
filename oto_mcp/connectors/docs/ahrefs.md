## prerequisite — clé api ahrefs

crée une clé API dans Ahrefs (Account → API Access — voir la [doc d'authentification](https://docs.ahrefs.com/en/api/docs/api-keys-creation-and-management)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée, chaque org consomme son propre abonnement Ahrefs
- un seat Ahrefs couvre plusieurs produits (Site Explorer, Keywords Explorer, Site Audit, Rank Tracker, Brand Radar…) — une seule clé les atteint tous

## usage — SEO, backlinks, mots-clés, rank tracking

ahrefs couvre l'essentiel de l'API Ahrefs v3, groupée par produit :
- « quel est le profil de liens / la position organique de ce domaine ? » → `ahrefs_site_explorer(report="domain-rating"|"organic-keywords"|"all-backlinks"|..., target="exemple.com")`
- « quel volume de recherche / difficulté pour ces mots-clés ? » → `ahrefs_keywords_explorer(report="overview", keywords="mot1,mot2", country="fr")`
- « quels problèmes techniques sur ce site crawlé ? » → `ahrefs_site_audit(report="issues", project_id=...)` (project_id via un projet Site Audit existant côté Ahrefs)
- « où ce mot-clé se positionne-t-il pour mes projets suivis ? » → `ahrefs_rank_tracker(report="overview", project_id=..., date=..., device="desktop")` (`project_id` via `ahrefs_project(op="list")`)
- « SERP pour n'importe quel mot-clé, sans projet Rank Tracker » → `ahrefs_serp_overview(keyword=..., country=...)`
- « comparer N domaines en un appel » → `ahrefs_batch_analysis(targets=[...], select=[...])`
- « où en est mon quota Ahrefs ? » → `ahrefs_account()` (gratuit)
- « quelle est ma visibilité de marque sur ChatGPT/Gemini/Perplexity… ? » → `ahrefs_brand_radar(report="mentions-overview", data_source="chatgpt,gemini", brand="MaMarque")`
- « analytics on-site (visiteurs, sources, géo, device) » → `ahrefs_web_analytics(report="stats"|"sources"|"countries"|..., project_id=...)` (nécessite le snippet JS Ahrefs installé sur le site suivi)
- « données Google Search Console » → `ahrefs_gsc(report="keywords"|"pages"|..., date_from=..., project_id=...)` (nécessite GSC connecté au projet côté Ahrefs)
- « gérer mes projets/mots-clés suivis » → `ahrefs_project`, `ahrefs_project_keywords`, `ahrefs_project_competitors`, `ahrefs_keyword_list`, `ahrefs_locations`
- « publier sur les réseaux sociaux connectés » → `ahrefs_social(op="publish", ...)`

## note — `select`, unités, et ce qui n'est PAS exposé en écriture

- la plupart des rapports Ahrefs exigent `select` (colonnes à retourner) — un jeu de colonnes par défaut est posé pour les rapports les plus utilisés (organic-keywords, top-pages, all-backlinks, refdomains, anchors, organic-competitors, pages-by-backlinks, keywords-explorer overview/matching-terms/related-terms, rank-tracker overview, serp-overview) ; ailleurs, `select` reste requis tel quel — voir [la doc Ahrefs](https://docs.ahrefs.com) pour les colonnes valides du rapport visé
- **ce `select` par défaut inclut des colonnes facturées au-delà du coût de base** (coûts vérifiés au spec OpenAPI d'Ahrefs, 2026-08-20) : `volume`/`keyword_difficulty`/`sum_traffic` (10 unités chacune) sur organic-keywords ; `sum_traffic`/`top_keyword_volume` (10 u.) sur top-pages ; `traffic_domain` (10 u.) sur refdomains ; `refdomains` (5 u.) sur anchors ; `traffic` (10 u.) sur organic-competitors/serp-overview ; `refdomains_target` (5 u.) sur pages-by-backlinks ; `volume`/`difficulty` (10 u. chacune) sur les 3 rapports keywords-explorer par défaut — délibéré (ce sont les colonnes utiles), mais à savoir avant de scaler `limit` sur ces reports
- la plupart des rapports défaultent à `limit=1000` lignes côté Ahrefs si omis ; `ahrefs_site_audit(report="issues"|"page-content")` coûte 50 unités/requête quel que soit `limit` — passer `limit` explicitement pour borner la dépense
- `extra` (dict) est l'échappatoire vers tout paramètre Ahrefs non typé ci-dessus (fusionné en dernier, prime sur les args typés)
- suppression/modification de ressources existantes (projet Rank Tracker, mots-clés suivis, compétiteurs, report Brand Radar) ne sont **pas** exposées — lecture et création seulement, par choix (même doctrine que Silae : une suppression est un acte délibéré, jamais un effet de bord)
- **vérification** : paramètres, corps de requête et colonnes `select` sont vérifiés mot pour mot contre le spec OpenAPI d'Ahrefs (`docs.ahrefs.com/openapi.json`, 2026-08-20) — pas contre un résumé de page doc. Aucun appel n'a en revanche été fait à la vraie API (pas de clé disponible pendant le build) : le spec dit ce qu'Ahrefs documente accepter, pas ce qu'il accepte réellement en prod
