## prerequisite — clé api linear

crée une clé API personnelle dans Linear (Settings → Security & access → Personal API keys — voir la [doc développeur](https://linear.app/developers)), puis colle-la dans oto.
- byo_org only : pas de clé oto partagée, et pas de clé personnelle par membre — une seule clé pour toute l'org (une clé API Linear est scopée au workspace)
- limites : 5 000 requêtes/heure et 3 000 000 points de complexité/heure par clé

## usage — issues, projets, cycles, équipes, labels, commentaires, webhooks

linear gère le suivi de tickets/projets de l'équipe, en 8 tools :
- « liste mes issues en cours sur l'équipe Produit » → `linear_issue(op="list", team_id="...", state_id="...")`
- « cherche les issues qui parlent de facturation » → `linear_issue(op="search", query="facturation")`
- « crée un ticket bug pour X » → `linear_issue(op="create", title="...", team_id="...", description="...")`
- « assigne cette issue à Jane et passe-la en cours » → `linear_issue(op="update", issue_id="...", assignee_id="...", state_id="...")`
- « quels sont les statuts possibles sur l'équipe Produit ? » → `linear_team(op="states", team_id="...")` (à faire avant un `update` qui change le statut — il faut le `state_id`)
- « commente cette issue » → `linear_comment(op="create", issue_id="...", body="...")`
- « les projets de l'équipe Produit » → `linear_project(op="list", team_id="...")`
- « crée un projet, puis passe-le à tel statut » → `linear_project(op="create", name="...", team_ids=["..."])` puis `op="update", status_id="..."` (`status_id` — pas un statut en texte libre, lis-le sur un projet existant via `op="get"|"list"`)
- « supprime ce projet/label de test » → `linear_project(op="delete", project_id="...")` / `linear_label(op="delete", label_id="...")`
- « le sprint en cours » → `linear_cycle(op="list", team_id="...")`
- « quels labels existent ? » → `linear_label(op="list", team_id="...")`
- « qui suis-je sur Linear ? » → `linear_user(op="viewer")`
- « préviens ce endpoint à chaque nouvelle issue » → `linear_webhook(op="create", url="...", team_id="...", resource_types=["Issue"])` (`resource_types` est requis par Linear, pas seulement `url`)

## note — testé en live, 6 bugs corrigés

Construit à partir de la documentation développeur Linear (GraphQL, pas de spec OpenAPI — Linear expose un schéma GraphQL, pas un contrat REST), puis **testé en live le 2026-08-21** contre un vrai workspace (introspection GraphQL + cycle complet create/get/update/delete/archive sur issues, commentaires, projets, labels, webhooks, tout nettoyé après coup). 6 vrais bugs trouvés et corrigés, aucun détectable par la seule introspection :
- `Project` n'a pas de champ `state` en lecture (c'est `status`, un objet) ni en écriture (`ProjectCreateInput`/`ProjectUpdateInput` n'ont que `statusId`) — d'où `status_id` plutôt qu'un statut en texte libre sur `linear_project`.
- Une opération GraphQL ne doit JAMAIS déclarer une variable qu'elle ne référence pas — un filtre optionnel omis faisait échouer `list_issues`/`list_projects`/`list_cycles`/`list_labels`/`search` dès qu'UN SEUL filtre parmi plusieurs était absent.
- `Query.webhooks` n'accepte aucun argument `filter`/`teamId` — le filtrage par équipe passe par `team(id:){webhooks{...}}`.
- `issueSearch` existe dans le schéma mais est mort en pratique (« This endpoint deprecated. ») — `linear_issue(op="search")` passe désormais par `issues(filter:{searchableContent:{contains:...}})`.
- Le filtre par équipe de `linear_project(op="list")` a besoin du wrapper `some:` (`accessibleTeams` est une collection, pas un filtre simple) — confirmé contre un vrai projet.
- La résolution d'une issue par son identifiant lisible (`"ENG-123"`) fonctionne, confirmé en live — identique à l'UUID.

## note — pas de préfixe `Bearer`

Contrairement à la plupart des API à clé de ce connecteur (Fireflies, Grain, Granola…), Linear attend la clé brute dans le header `Authorization`, sans préfixe `Bearer` — une spécificité documentée par Linear elle-même, pas une découverte empirique.
