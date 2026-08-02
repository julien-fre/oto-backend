## prerequisite — clé api insee sirene

les outils d'identité, recherche, bilans et événements (`fr_search`, `fr_get`, `fr_bilans`, `fr_events`…) tournent en open data, **sans clé**.
une clé n'est requise que pour les appels **insee sirene** (`fr_siret`, `fr_headquarters` — siret/siège à la source officielle).
- crée un compte sur le [portail api insee](https://api.insee.fr) et souscris à l'api sirene
- récupère ta clé, puis pose-la sur ton dashboard oto (connecteur `sirene`)
- les requêtes **sirene stock** (`fr_stock_*`, parquet local) n'ont **pas** besoin de clé

## usage — données entreprise france

interroge identité, finances, dirigeants, événements légaux et appels d'offres d'une entreprise française.
- `fr_search(query=…, naf=…, departement=…)` — recherche multicritère (secteur, zone, effectifs, CA)
- `fr_get(siren)` — fiche complète agrégée : identité + 7 ratios du dernier bilan inpi + événements bodacc
- `fr_bilans(siren)` puis `fr_bilan(siren, date_cloture)` — historique des dépôts et bilan détaillé (CA, EBE, endettement…)
- `fr_directors(siren)`, `fr_events(siren)`, `fr_tenders_search(query=…)` — dirigeants, événements bodacc, appels d'offres boamp
- `fr_accords_search(siren=…)`, `fr_egapro_declaration(siren)`, `fr_avis_sirene(siret)` — accords d'entreprise, index égalité f-h, avis de situation insee (pdf)

## usage — aides publiques (subventions, prêts, aap)

la base de référence de l'état (data.aides-entreprises.fr, ~2 400 aides actives, màj quotidienne) filtrée pour une entreprise ou un projet.
- `fr_aides_search(insee=…, effectif=…, nature=…, echeance_avant=…)` — shortlist déterministe : territoire (commune → région → national/ue), tranche d'effectif, type d'aide (subvention, prêt, garantie…), échéance des aap
- `fr_aides_get(id)` — fiche complète : objet, conditions, montants, financeurs, contacts, source officielle

## usage — sirene stock (enrichissement en masse)

le parquet sirene complet (insee, millésime mensuel) pour les lookups ponctuels et l'enrichissement **batch** de milliers de sirens.
- `fr_stock_enrich(sirens=[…])` — sièges d'une **liste** de sirens en un seul scan (bulk)
- `fr_stock_siege(siren)` / `fr_stock_etablissements(siren)` — siège ou tous les établissements d'une boîte
- `fr_stock_search(naf=…, enseigne=…, departement=…)` — énumère tous les sites (ex. tous les « intermarché » d'un département)
