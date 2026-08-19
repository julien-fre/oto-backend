## prerequisite — clé api lightfield

crée une clé API dans Lightfield (Settings → API keys, admin uniquement — [doc](https://docs.lightfield.app/using-the-api/api-keys/)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée — ce sont les données de ton CRM, chaque organisation pose la sienne
- ⚠️ **les scopes se choisissent à la CRÉATION de la clé et ne s'ajoutent pas après coup.** Coche au minimum `accounts:read`, `contacts:read`, `opportunities:read` ; pour écrire, ajoute les `:create` et `:update` correspondants ; pour l'envoi d'email, `emails:create`. Une clé sans lecture CRM est refusée par le bouton « tester la connexion », qui te dira les scopes réellement accordés
- l'envoi d'email exige en plus une boîte Google ou Microsoft **connectée dans Lightfield** par le propriétaire de la clé

## usage — le CRM dont les champs t'appartiennent

le modèle de champs de Lightfield est propre à CHAQUE workspace : les clés sont celles que ton équipe a créées, pas des noms universels.
- avant la première écriture → `lightfield_accounts(op="definitions")` (idem contacts, opportunités, notes, tâches) : c'est la liste des clés valides
- « quelles sociétés du CRM correspondent à… » → `lightfield_accounts(op="search", filters={...})`
- « l'état à jour de cette société » → `lightfield_accounts(op="get", record_id="…")`
- « crée / mets à jour cette société » → `lightfield_accounts(op="upsert", fields={...})` (ajoute `record_id` pour mettre à jour)
- objets personnalisés → `lightfield_objects(op="list")` puis `op="definitions"` sur le slug rendu
- écrire un email depuis la boîte connectée → `lightfield_emails(op="send", sender="…", to=[...], dry_run=False)`

## note — trois pièges qui coûtent cher

- ⚠️ **`op="search"` lit un index qui peut être en retard.** Après une écriture, relis par `op="get"` : la recherche peut rendre l'état d'AVANT. C'est écrit dans la doc éditeur, et ça se voit surtout quand on enchaîne écrire-puis-vérifier
- ⚠️ **`limit` plafonne à 25** (imposé par l'API) : au-delà, paginer avec `offset`
- ⚠️ **l'envoi ne sait ni répondre ni transférer** : `op="send"` crée TOUJOURS un message neuf, jamais une réponse dans un fil existant — si le contexte compte, cite-le toi-même dans le corps. Et `op="send"` est en **dry-run par défaut** : il faut `dry_run=False` pour que quelque chose parte
