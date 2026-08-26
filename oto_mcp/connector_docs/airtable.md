## prerequisite — personal access token airtable

crée un **personal access token** sur [airtable.com/create/tokens](https://airtable.com/create/tokens), puis colle-le dans oto (il commence par `pat…`).
- deux réglages, PAS un seul — c'est le piège n°1 d'airtable :
  1. **scopes** : `data.records:read` + `data.records:write`, `data.recordComments:read` + `data.recordComments:write`, `schema.bases:read` + `schema.bases:write`
  2. **access** : ajouter explicitement chaque base (ou le workspace entier) que le token doit voir
- un token avec tous les scopes mais **aucune base accordée** répond `200` avec une liste vide, jamais une erreur : le bouton « tester la connexion » d'oto le détecte et le dit
- byo-only : pas de clé oto partagée — un token airtable est lié à un compte et à des bases précises

## usage — lire et écrire dans une base

- « quelles bases je peux atteindre ? » → `airtable_base()` (rend les `appXXXXXXXX` — le point de départ de tout le reste)
- « qu'est-ce qu'il y a dans cette base ? » → `airtable_table(base_id="app…")` (tables, champs, types, options, vues)
- « quelles colonnes exactement, avant d'écrire ? » → `airtable_field(base_id="app…", table_id="tbl…")`
- « les lignes où le statut est Done » → `airtable_record(base_id="app…", table="tbl…", filter_by_formula="{Status}='Done'")`
- « ajoute ces 40 prospects » → `airtable_record(op="create", records=[{"Nom": "…", "Email": "…"}, …])` (découpé en lots de 10 tout seul)
- « mets à jour si l'email existe, crée sinon » → `airtable_record(op="upsert", records=[…], merge_on=["Email"])`
- « corrige cette ligne » → `airtable_record(op="update", record_id="rec…", fields={"Statut": "Signé"})`
- « commente cette ligne » → `airtable_comment(op="create", base_id=…, table=…, record_id=…, text="relance envoyée")`
- « joins ce PDF » → `airtable_attachment(base_id=…, record_id=…, field="Pièces jointes", filename="devis.pdf", content_type="application/pdf", file_base64=…)`
- « crée une table de suivi » → `airtable_table(op="create", name="Suivi", fields=[{"name": "Nom", "type": "singleLineText"}, {"name": "Statut", "type": "singleSelect", "options": {"choices": [{"name": "À faire"}, {"name": "Fait"}]}}])`

## note — noms vs identifiants, typecast, lots

- **utilise les identifiants, pas les noms** : `tbl…`, `fld…` sont stables ; un nom de table ou de colonne change dès que quelqu'un le renomme dans l'interface, et l'automatisation casse en silence. `airtable_table` et `airtable_field` rendent les identifiants.
- **`typecast` est désactivé par défaut, exprès** : chez airtable ce n'est pas une conversion de confort mais une **modification du schéma déclenchée par une écriture de donnée** — il crée l'option manquante d'un select, voire un enregistrement dans la table liée d'un champ *linked record*. écrire « Signé » dans un select qui ne connaît que « Signee » échoue donc franchement au lieu d'ajouter une option en double. passer `typecast=True` quand on VEUT ce comportement.
- **`replace=True` sur un update est destructif** : c'est un PUT, toute colonne non transmise est VIDÉE. le défaut (PATCH) ne touche que les colonnes envoyées.
- **lots** : airtable refuse plus de 10 lignes par requête et 5 requêtes/seconde par base. oto découpe et espace tout seul, jusqu'à 200 lignes par appel. si airtable coupe pour cause de débit, la réponse porte `aborted: "rate_limit"` et le nombre de lignes réellement écrites — relancer 30 secondes plus tard avec le reste.
- **listes longues** : `airtable_record` suit la pagination jusqu'à `max_records` (100 par défaut) et pose `more: true` + `offset` s'il reste des lignes. rien n'est tronqué en silence.
- **pièces jointes > 5 Mo** : héberger le fichier et écrire son URL dans la colonne — `airtable_record(op="update", fields={"Pièces jointes": [{"url": "https://…"}]})`. airtable va le chercher lui-même.
- **`airtable_sync` n'est pas un import CSV** : il alimente une table créée dans airtable via « Sync from other sources → API », et chaque envoi REMPLACE le contenu synchronisé. pour charger des lignes dans une table ordinaire, c'est `airtable_record(op="create")`.
- **rien ne se supprime dans le schéma, et c'est vérifié** : pas de `DELETE` de champ (404), pas de suppression de table, et un `PATCH` de champ portant `options` est refusé (`422` « Changing a field's type or number precision is not currently supported »). concrètement : une option de select créée par erreur avec `typecast=True` ne se retire QUE dans l'interface Airtable. une raison de plus de laisser `typecast` à faux.
- **supprimer une base est impossible par l'API** : `airtable_base(op="create")` n'a pas de réciproque, ça se fait dans l'interface.
