## prerequisite — clé api linear

crée une clé API personnelle dans Linear (Settings → Security & access → Personal API keys — voir la [doc développeur](https://linear.app/developers)), puis colle-la dans oto.
- byo_org only : pas de clé oto partagée, et pas de clé personnelle par membre — une seule clé pour toute l'org (une clé API Linear est scopée au workspace)
- limites : 5 000 requêtes/heure et 3 000 000 points de complexité/heure par clé

## usage — issues, projets, cycles, équipes, labels, commentaires, webhooks

linear gère le suivi de tickets/projets de l'équipe, en 8 tools :
- « liste mes issues en cours sur l'équipe Produit » → `linear_issue(op="list", team_id="...", state_id="...")`
- « cherche les issues qui parlent de facturation » → `linear_issue(op="search", query="facturation")`
- « crée un ticket bug pour X » → `linear_issue(op="create", title="...", team_id="...", description="...")`
- « assigne cette issue à Julien et passe-la en cours » → `linear_issue(op="update", issue_id="...", assignee_id="...", state_id="...")`
- « quels sont les statuts possibles sur l'équipe Produit ? » → `linear_team(op="states", team_id="...")` (à faire avant un `update` qui change le statut — il faut le `state_id`)
- « commente cette issue » → `linear_comment(op="create", issue_id="...", body="...")`
- « les projets de l'équipe Produit » → `linear_project(op="list", team_id="...")`
- « le sprint en cours » → `linear_cycle(op="list", team_id="...")`
- « quels labels existent ? » → `linear_label(op="list", team_id="...")`
- « qui suis-je sur Linear ? » → `linear_user(op="viewer")`
- « préviens ce endpoint à chaque nouvelle issue » → `linear_webhook(op="create", url="...", team_id="...", resource_types=["Issue"])`

## note — testé sans clé live

Construit à partir de la documentation développeur Linear (GraphQL, pas de spec OpenAPI — Linear expose un schéma GraphQL, pas un contrat REST). Aucune clé Linear n'était disponible en session de construction : traiter ce connecteur comme non vérifié tant qu'il n'a pas été exercé contre un vrai workspace. En particulier, la résolution d'une issue par son identifiant lisible (`"ENG-123"`, par opposition à l'UUID) sur `linear_issue(op="get")` n'est pas confirmée.

## note — pas de préfixe `Bearer`

Contrairement à la plupart des API à clé de ce connecteur (Fireflies, Grain, Granola…), Linear attend la clé brute dans le header `Authorization`, sans préfixe `Bearer` — une spécificité documentée par Linear elle-même, pas une découverte empirique.
