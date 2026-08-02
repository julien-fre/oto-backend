## prerequisite — obtenir une clé apollo

crée une clé api dans les réglages développeur/api de ton compte [apollo](https://app.apollo.io).
- colle-la dans tes connecteurs oto sur `/account` — apollo est **byo** (pas de clé plateforme)
- la clé hérite des crédits de ton plan apollo

## usage — prospection b2b (entreprises + contacts)

recherche et enrichis entreprises et personnes, et repère les signaux de recrutement.
- `apollo_search_organizations` — entreprises par nom, domaine, pays
- `apollo_search_people` — personnes par domaines, départements, intitulés, séniorités
- `apollo_match_person` — enrichit une personne (url linkedin ou email = meilleurs identifiants)
- `apollo_job_postings` — offres d'emploi actives d'une entreprise (signal d'embauche)
