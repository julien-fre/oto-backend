## prerequisite — tes tables clay (et, en option, ta clé api)

une seule carte clay, plusieurs entrées nommées — chacune a un **type** :
- `table` : une table clay où oto écrit des lignes. dans clay, ouvre la table → **+ add** → **monitor webhook**, copie la **commande cURL** affichée et colle-la telle quelle dans le champ webhook (l'url et le jeton d'auth sont repris). le **nom** de l'entrée est celui que l'agent utilisera pour viser la table
- `api` : ta clé clay public api (clay → settings → account → api keys). nécessaire seulement pour lancer des routines, chercher dans la base clay ou lire des tables. la clé est **personnelle** : elle dépense tes crédits clay
- clay n'a pas d'api pour créer un webhook de table : chaque table s'ajoute depuis l'ui clay, une fois

## usage — écrire des lignes dans une table clay

- `clay_list_tables` liste les tables enregistrées (nom, niveau, lignes déjà envoyées)
- `clay_push_rows(table, row=…)` envoie une ligne ; `rows=[…]` jusqu'à 50 lignes par appel, avec un reçu `{total, succeeded, failed}`
- une ligne = un objet json = un envoi (un tableau json ne fait qu'UNE ligne). l'objet entier arrive dans la colonne **webhook** de la table ; ses clés se relient aux colonnes une fois, dans clay. clay lance ensuite les enrichissements de la table sur chaque nouvelle ligne (crédits clay du propriétaire)
- webhook protégé par un jeton : sans jeton ou avec un mauvais, clay répond 401 et le lot s'arrête au premier refus
- `dry_run=True` valide et montre ce qui partirait, sans rien envoyer
- si la table demandée n'existe pas, le refus liste les tables connues : demande à l'utilisateur d'ajouter la bonne sur la carte clay plutôt que d'en deviner une

## usage — routines, recherche et tables via l'api

- `clay_account` : à qui appartient la clé, et le solde de crédits du workspace
- `clay_run_routine(routine_id, items)` lance une routine (fonction ou workflow clay) sur 1 à 100 items `{id, inputs}`. c'est **asynchrone** : l'appel rend un `routine_run_id`, puis `clay_get_run` jusqu'à `status = complete` (quelques secondes entre deux appels). aucun endpoint ne liste les routines : l'id (`function:t_…`) se demande à l'utilisateur
- `clay_search` cherche people/companies dans la base clay : mode filtres (`source_type` + `filters`, champs via `clay_search_fields`) ou mode requête (`query`, grammaire via `clay_search_reference`). page suivante : `clay_search_next(search_id, mode)`
- `clay_tables_query` lit des lignes de tables existantes — **plan enterprise** de clay uniquement

## note — limites de clay

- un webhook de table accepte **50 000 envois au total**, même si on supprime des lignes. oto compte ce qu'il envoie et prévient à l'approche ; au-delà, crée un nouveau webhook dans la table et recolle-le sur la même entrée (le compteur repart de zéro)
- chaque appel consomme les crédits clay du compte concerné, comme le même travail fait dans l'ui clay
- rate limit par workspace : un refus 429 indique le délai d'attente (`retry_after`) — attends-le avant de réessayer
