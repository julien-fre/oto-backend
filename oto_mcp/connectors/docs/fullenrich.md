## prerequisite — obtenir une clé fullenrich

crée une clé api dans les réglages api de ton compte [fullenrich](https://app.fullenrich.com).
- colle-la dans tes connecteurs oto sur `/account` — fullenrich est **byo** (chacun sa clé)
- facturation **au résultat** : 10 crédits/téléphone, 1/email pro, 3/email perso, rien si aucune donnée trouvée

## usage — enrichissement waterfall (20+ sources)

trouve téléphones et emails d'un contact en cascade sur 20+ fournisseurs (~70% de taux sur le téléphone).
- `fullenrich_enrich_linkedin` — soumet un job **bulk** (1 à 100 contacts : prénom/nom + slug linkedin + entreprise), retour immédiat avec un `enrichment_id`
- `fullenrich_result` — relève le résultat : repasser après `retry_after_s` jusqu'à `done` (un job prend ~30s à 4 min). Passer le `submitted_at` rendu à la soumission : au-delà de 20 min, la réponse dit d'arrêter (`verdict`) — ne pas resoumettre, ce serait facturé deux fois. Un job annulé, inconnu ou expiré est un refus nommé, pas un statut à relever. Relever ne consomme pas le quota plateforme.
- renvoie téléphones, emails pro et perso, titre et localisation par contact
