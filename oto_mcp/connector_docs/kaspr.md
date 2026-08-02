## prerequisite — obtenir une clé kaspr

crée une clé api dans les réglages api/intégrations de ton compte [kaspr](https://app.kaspr.io).
- colle-la dans tes connecteurs oto sur `/account` — kaspr est **byo** (pas de clé plateforme, chacun la sienne)
- kaspr facture en crédits : 1 par email, +1 par téléphone
- vérifie ta clé avec `oto_instance(op='verify', connector='kaspr')`

## usage — enrichir un contact depuis linkedin

récupère emails et téléphones d'une personne à partir de son profil linkedin.
- `kaspr_enrich_linkedin` — passe le slug (`alexis-laporte`) ou l'url linkedin complète, options `with_phone` pour les numéros
