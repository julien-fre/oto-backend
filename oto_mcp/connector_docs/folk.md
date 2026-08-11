## prerequisite — ta clé api folk

folk fournit une clé api personnelle. récupère-la dans les [réglages api/développeur de ton compte folk](https://app.folk.app) (doc : [developer.folk.app](https://developer.folk.app)).
- colle-la dans oto sur ton compte (`/account`), connecteur **folk**
- byo uniquement : ta clé, ou le credential partagé de ton org — pas de clé plateforme
- les **groupes** ne se créent pas via l'api : crée-les dans l'app folk, puis référence-les par leur id

## usage — ce que tu peux faire

gère ton crm folk (personnes, entreprises, deals) + notes, interactions et rappels depuis claude.
- « trouve le contact dupont » → `folk_record(op="search")` (entity `person`), puis `folk_record(op="get")` pour la fiche
- « ajoute jean dupont, cto chez acme » → `folk_record(op="create")` (entity `person`)
- « log un appel sur ce contact » → `folk_record(op="create")` (entity `interaction`, type/titre/contenu)
- « crée un deal dans le groupe X » → `folk_record(op="create")` (entity `deal`), et `folk_record(op="search", entity="deal")` pour les lister
- « ajoute ces 20 contacts » → `folk_record(op="create")` (entity `person`, `items=[...]`) en un seul appel
- « préviens mon endpoint à chaque nouveau deal du groupe X » → `folk_webhook(op="create")` (avant ça : `folk_group(op="list")` pour l'id du groupe, `folk_group(op="custom_fields")` si le filtre porte sur un champ custom)
- « liste mes webhooks » / « désactive ce webhook » → `folk_webhook(op="list")` / `folk_webhook(op="update")` (`fields={"status": "inactive"}`)
- ⚠️ un filtre de webhook posé via l'api n'existe QUE là : le modifier depuis les réglages de l'app folk le fait disparaître silencieusement
