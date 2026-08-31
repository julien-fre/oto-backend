## prerequisite — ta clé api tally

crée une clé sur [tally.so/settings/api-keys](https://tally.so/settings/api-keys) (Settings → API keys → Create API key), puis colle-la dans oto. Elle n'est affichée qu'une fois.
- byo-only : pas de clé oto partagée, et ce n'est pas un choix de catalogue — une clé Tally est liée à **un utilisateur**, hérite de SES droits (Tally n'offre aujourd'hui aucun scope fin) et **cesse de fonctionner s'il quitte l'organisation**. Une clé « de service » n'existe pas : pose celle d'un compte qui restera.
- format `tly-…`, envoyée en `Authorization: Bearer`

## usage — lire les réponses, piloter les formulaires

six tools, verbe en `op` :
- « quelles réponses sont arrivées depuis la dernière fois ? » → `tally_submission(op="list", form_id="…", after_id="<dernier id vu>")`
- « donne-moi cette réponse » → `tally_submission(op="get", form_id="…", submission_id="…")`
- « quels formulaires ai-je ? » → `tally_form(op="list")`, puis `op="get"` / `op="questions"` / `op="blocks"`
- « crée / modifie un formulaire » → `tally_form(op="create"|"update"|"update_blocks", blocks=[…])` (39 types de blocs, cf. [blocks-reference](https://developers.tally.so/blocks-reference))
- « où les gens abandonnent-ils ce formulaire ? » → `tally_analytics(op="drop_off", form_id="…", period="30d")`
- « mes espaces de travail, mes dossiers » → `tally_workspace(op="list"|"folders"|…)`
- « qui est dans l'organisation, invite quelqu'un » → `tally_account(op="me")` (c'est lui qui rend `organizationId`) puis `op="users"|"invite"|"invites"`
- « pousse chaque réponse vers mon système » → `tally_webhook(op="create", form_id="…", url="https://…", event_types=["FORM_RESPONSE"], signing_secret="…")`

## note — la jointure des réponses, et cinq pièges

- **les réponses ne sont pas auto-descriptives** : l'API rend `questions` une fois par page et chaque réponse pointe dedans par `questionId`. `tally_submission` fait la jointure — chaque réponse porte `answers: [{question_id, title, type, answer, formatted}]`, plus `answers_by_title` **seulement si les titres sont uniques** sur ce formulaire (sinon la clé est absente et `title_collisions` dit lesquels se marchent dessus). `raw=True` rend la charge Tally intacte.
- **une question `FILE_UPLOAD` répond par l'URL du fichier déposé** : c'est par `answers` qu'on atteint les pièces jointes, il n'y a pas d'endpoint de fichiers séparé. Chaque réponse porte aussi `pdf_url` et `preview_url` — Tally rend la réponse complète en PDF, inutile de la reconstituer.
- **`tally-version` est épinglé côté client.** Tally versionne par DATE (à la Stripe) et une clé est figée à la version du jour de sa création, sans possibilité de la changer. Sans en-tête explicite, deux clients de la même org obtiendraient des formes de réponse différentes selon l'ancienneté de leur clé — c'est exactement ce qui fait qu'une clé de 2025 ne rend pas `formattedAnswer`. Le client envoie donc toujours `tally-version` (défaut `2026-08-04`).
- **`PATCH /webhooks/{id}` est un REMPLACEMENT complet** malgré le verbe (`formId`, `url`, `eventTypes`, `isEnabled` tous requis). `tally_webhook(op="update")` relit le webhook et fusionne — ne le contourne pas en tapant l'API à la main.
- **`blocks` remplace la liste entière** sur `op="update"` et `op="update_blocks"` : un bloc absent du tableau est supprimé. Lis l'état avec `op="blocks"` d'abord.
- **oto n'est pas un récepteur de webhooks.** `tally_webhook` enregistre une URL que TU contrôles (n8n, Make, ton service) ; pour faire entrer les réponses dans oto, planifie un `tally_submission(op="list", after_id=…)`. Les livraisons de webhook, elles, ne consomment pas le quota de 100 req/min — le polling si.

## note — ce qui détruit, et le filet

quatre opérations détruisent quelque chose ; toutes acceptent `dry_run=True`, qui valide à l'identique et rend un diff réel au lieu d'écrire :
- `tally_submission(op="delete")` — **irréversible** : Tally documente une corbeille pour les formulaires et les espaces, **pas pour les réponses**
- `tally_account(op="remove_user")` — sort quelqu'un de l'organisation **et révoque toutes les clés API qu'il a créées**, y compris possiblement celle de l'appel en cours
- `tally_workspace(op="delete")` et `op="delete_folder"` — emportent les formulaires contenus (corbeille, restaurables)
- `tally_form(op="delete")` — corbeille, restaurable

## note — état de vérification

dérivé du spec OpenAPI 3.0.1 réel (`developers.tally.so/api-reference/openapi.json`, lu le 2026-08-31) : `required`, formes de corps, enums, bornes de `limit` (réponses et formulaires 1-500 défaut 50, webhooks 1-100 défaut 25), périodes d'analytics. **Pas encore testé en live** — aucune clé `tly-` n'était disponible à l'écriture. Restent à confirmer contre une vraie clé : la valeur par défaut de `tally-version`, la forme exacte des enveloppes de liste (`/webhooks`, `/workspaces/{id}/folders`, `/organizations/{id}/users` — tableau nu ou objet paginé, le spec ne trancheant pas partout), et le code de retour des `DELETE`.
