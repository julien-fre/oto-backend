## prerequisite — ta clé api folk

folk fournit une clé api personnelle. récupère-la dans les [réglages api/développeur de ton compte folk](https://app.folk.app) (doc : [developer.folk.app](https://developer.folk.app)).
- colle-la dans oto sur ton compte (`/account`), connecteur **folk**
- byo uniquement : ta clé, ou le credential partagé de ton org — pas de clé plateforme
- les **groupes** (et leurs champs custom) se listent/créent/modifient via l'api (`folk_group`) — la suppression d'un groupe ou d'un champ custom reste réservée à l'app folk ; retirer un **membre** d'un groupe, en revanche, se fait via l'api (`folk_group(op="remove_member")`)

## usage — ce que tu peux faire

gère ton crm folk (personnes/contacts, entreprises, deals ou tout autre objet custom) + notes, interactions et tâches depuis claude. Tout passe par `folk_record` (paramètre `entity`) — il n'y a pas de tool séparé `folk_company`/`folk_contact`/`folk_deal`.
- « trouve le contact dupont » → `folk_record(op="search")` (entity `person`), puis `folk_record(op="get")` pour la fiche
- « ajoute jean dupont, cto chez acme » → `folk_record(op="create")` (entity `person`)
- « log un appel sur ce contact » → `folk_record(op="create")` (entity `interaction`, type/titre/contenu)
- « qu'est-ce qu'on s'est dit avec dupont ? » → `folk_record(op="search", entity="interaction", entity_id="per_…")` : emails, événements d'agenda, messages whatsapp et interactions loggées que folk garde sur cette fiche, puis `op="get"` (avec le même `entity_id`) pour le corps complet d'une seule
  - ⚠️ **correctif** : ce document a longtemps affirmé que folk ne disait QUE *quand* on avait parlé à quelqu'un, jamais *quoi*. C'était faux — c'était vrai du connecteur, qui n'exposait que la création d'interaction, pas de folk, dont les endpoints de lecture existaient déjà (en open beta)
  - une interaction n'est pas adressable seule : `entity_id` (la personne/société porteuse) est **obligatoire** en recherche, lecture et suppression
  - `when="past"` par défaut (ce qui a eu lieu) ; `"upcoming"` pour ce qui est prévu, `"all"` pour les deux — le défaut MASQUE donc l'à-venir
  - le corps peut revenir vide sans que l'interaction le soit : chaque interaction porte un `privacyLevel`, et `subjectOnly`/`sensitive`/`internal` cachent le contenu à qui n'était pas dans la conversation. la clé est byo → tu vois ce que voit SON propriétaire
- « qu'est-ce qui reste ouvert sur ce contact ? » → `folk_record(op="search", entity="task", entity_id="per_…", filters={"completedAt": {"empty": True}})` ; pour clore : `folk_record(op="mark_done", ids=[...])` (jusqu'à 50 d'un coup), `op="mark_todo"` pour rouvrir
  - ⚠️ **rappels dépréciés** : folk a déprécié `/reminders` le 13/08/2026 (retrait annoncé février 2027) au profit des **tâches**, qui font strictement plus (description markdown, filtres par échéance/assigné/complétion, et un vrai suivi de complétion). `entity="reminder"` marche encore, mais écris tout ce qui est nouveau en `entity="task"`
  - une tâche ne se termine JAMAIS toute seule chez folk (contrairement à un rappel qui se marque « déclenché ») : `completedAt` ne bouge que sur un `mark_done`/`mark_todo` explicite — et il est refusé dans un `op="update"`
  - ❓ non documenté par folk et non vérifié : les rappels DÉJÀ posés remontent-ils aussi dans `entity="task"` ? à trancher avant de considérer un rappel comme lisible côté tâches
  - ⚠️ pas d'événement webhook `task.*` chez folk à ce jour — seuls les `reminder.*` existent
- « crée un deal dans le groupe X » → `folk_record(op="create")` (entity `deal`), et `folk_record(op="search", entity="deal")` pour les lister — `object_type` est auto-découvert si omis (voir note ci-dessous) ; ne le passer explicitement que si le groupe a PLUSIEURS objets custom au-delà de person/company (l'auto-découverte lève alors une erreur qui les liste)
- « ajoute ces 20 contacts » → `folk_record(op="create")` (entity `person`, `items=[...]`) en un seul appel
- « crée un groupe Leads privé » → `folk_group(op="create", name="Leads", visibility="private")`
- « ajoute un champ Statut (select) sur les personnes du groupe X » → `folk_group(op="create_custom_field")` (`entity_type="person"` par défaut, `custom_field={"type": "singleSelect", "name": "Statut", "options": [...]}`)
- ⚠️ seuls `person`/`company` sont des entity_type FIXES. Tout objet au-delà (deal ou autre) est un **objet custom que chaque client folk nomme lui-même** ("Deals" n'est que le nom choisi par CE workspace — un autre pourrait l'appeler "Opportunités", au singulier, etc.). Pour `folk_group(op="custom_fields"/...)`, découvrir le nom : appeler avec n'importe quel entity_type — le 404 de Folk liste les VRAIS entity_type de ce groupe (`"Available entity types are: ..."`), puis rappeler avec le bon nom. `folk_record`'s `object_type` fait cette découverte tout seul (voir note ci-dessus) — seul `folk_group` demande encore de la faire à la main
- « ajoute jean comme admin du groupe Leads » → `folk_group(op="add_member")` (`user_id` depuis `folk_user(op="list")`, `role="admin"`)
- « qui a accès au groupe Leads ? » / « retire jean du groupe » → `folk_group(op="members")` / `folk_group(op="remove_member")`
- ⚠️ un groupe **public** (`visibility="public"`) a une appartenance IMPLICITE : `folk_group(op="members")` y liste TOUT le workspace en rôle "admin", que quelqu'un ait été ajouté ou non (vérifié en live) — `add_member`/`remove_member`/`update_member` n'ont d'effet réel que sur un groupe **private** (appartenance explicite)
- « préviens mon endpoint à chaque nouveau deal du groupe X » → `folk_webhook(op="create")` (avant ça : `folk_group(op="list")` pour l'id du groupe, `folk_group(op="custom_fields")` si le filtre porte sur un champ custom)
- « liste mes webhooks » / « désactive ce webhook » → `folk_webhook(op="list")` / `folk_webhook(op="update")` (`fields={"status": "inactive"}`)
- ⚠️ un filtre de webhook posé via l'api n'existe QUE là : le modifier depuis les réglages de l'app folk le fait disparaître silencieusement
