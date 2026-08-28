## prerequisite — obtenir une clé hunter.io

crée une clé api dans les réglages api de ton compte [hunter.io](https://hunter.io).
- colle-la dans tes connecteurs oto sur `/account`
- les membres peuvent utiliser la clé plateforme (quota quotidien) ; un guest doit poser la sienne
- hunter facture en crédits (1 crédit par appel, 1 par tranche de 10 emails sur le domain search)

## usage — trouver et vérifier des emails

découvre les emails d'une entreprise, devine celui d'une personne, et vérifie sa délivrabilité.
- `hunter_domain_search` — liste les emails publics trouvés sur un domaine + le pattern d'adresse
- `hunter_email_finder` — l'email d'une personne précise dans une boîte (nom + domaine)
- `hunter_email_verify` — vérifie qu'une adresse est délivrable
