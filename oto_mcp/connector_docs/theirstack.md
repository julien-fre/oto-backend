## prerequisite — clé api theirstack

crée une clé API dans TheirStack (Settings → API keys — voir la [doc d'authentification](https://theirstack.com/en/docs/api-reference/authentication)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée, chaque compte/org consomme ses propres crédits

## usage — qui recrute quoi, et avec quels outils

theirstack agrège les offres d'emploi publiées par les entreprises (sites carrière + job boards, 100+ pays) et en déduit les technologies qu'elles utilisent (ERP, CRM, e-commerce…).
- « est-ce que cette entreprise recrute en ce moment ? quels postes ? » → `theirstack_jobs_search(company_names=["Nom Exact"])` (offres des 90 derniers jours par défaut)
- « quels grossistes français utilisent tel ERP ? » → `theirstack_companies_search(company_country_code_or=["FR"], extra={"company_technology_slug_or": ["sap"]})`
- « fiche techno + effectif de ces entreprises » → `theirstack_companies_search(company_names=[...])` (rend nom, domaine, effectif, secteur, technologies)
- besoin du record complet (description d'offre, salaires, équipe qui recrute, revenus…) → `full=True`

## note — crédits, couverture et noms exacts

- les crédits se comptent au record ENTREPRISE rendu : un crédit entreprise déverrouille toutes ses offres + technologies + firmographie ; `limit` borne la dépense, `metadata.truncated_*` dit ce qui n'a pas été rendu faute de crédits
- couverture partielle sur les petites entreprises (≈ 8 % des petits grossistes français vus dans le pilote) : une réponse `data: []` est NORMALE, pas une erreur — inutile de réessayer
- `company_names` est une correspondance EXACTE et sensible à la casse ; pour élargir, passer `company_name_case_insensitive_or`, `company_name_partial_match_or` ou `company_domain_or` dans `extra`
- `extra` ouvre toute la DSL éditeur (~110 filtres jobs, ~60 entreprises) : voir la [référence API](https://theirstack.com/en/docs/api-reference)
