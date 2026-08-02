## prerequisite — ta clé api folk

folk fournit une clé api personnelle. récupère-la dans les [réglages api/développeur de ton compte folk](https://app.folk.app) (doc : [developer.folk.app](https://developer.folk.app)).
- colle-la dans oto sur ton compte (`/account`), connecteur **folk**
- byo uniquement : ta clé, ou le credential partagé de ton org — pas de clé plateforme
- les **groupes** ne se créent pas via l'api : crée-les dans l'app folk, puis référence-les par leur id

## usage — ce que tu peux faire

gère ton crm folk (personnes, entreprises, deals) + notes, interactions et rappels depuis claude.
- « trouve le contact dupont » → `folk_search` (entity `person`), puis `folk_get` pour la fiche
- « ajoute jean dupont, cto chez acme » → `folk_create` (entity `person`)
- « log un appel sur ce contact » → `folk_create` (entity `interaction`, type/titre/contenu)
- « crée un deal dans le groupe X » → `folk_create` (entity `deal`), et `folk_list_deals` pour les lister
- « ajoute ces 20 contacts » → `folk_create` (entity `person`, `items=[...]`) en un seul appel
