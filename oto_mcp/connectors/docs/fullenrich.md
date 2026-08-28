## prerequisite — obtenir une clé fullenrich

crée une clé api dans les réglages api de ton compte [fullenrich](https://app.fullenrich.com).
- colle-la dans tes connecteurs oto sur `/account` — fullenrich est **byo** (chacun sa clé)
- facturation **au résultat** : 10 crédits/téléphone, 1/email pro, 3/email perso, rien si aucune donnée trouvée

## usage — enrichissement waterfall (20+ sources)

trouve téléphones et emails d'un contact en cascade sur 20+ fournisseurs (~70% de taux sur le téléphone).
- `fullenrich_enrich_linkedin` — soumet un job **bulk** (1 à 100 contacts : prénom/nom + slug linkedin + entreprise), retour immédiat avec un `enrichment_id`
- `fullenrich_result` — relève le résultat (repasser toutes les ~20-30s jusqu'à `done` ; un job prend ~30s à 4 min)
- renvoie téléphones, emails pro et perso, titre et localisation par contact
