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
- **une question `FILE_UPLOAD` répond par une LISTE de fichiers** — `[{id, name, url, mimeType, size}]`, vérifié en live. Les trois métadonnées `name`/`mimeType`/`size` arrivent DANS la réponse : de quoi vérifier qu'une pièce est présente et au bon format **sans rien télécharger** (ce qui rend un contrôle de complétude possible avant même qu'un DPA n'autorise à lire le contenu). Chaque réponse porte aussi `pdf_url` et `preview_url` — Tally rend la réponse complète en PDF, inutile de la reconstituer.
- ⚠️ **ces trois URL portent un jeton signé** (`accessToken` = un JWT, plus `signature`, dans la query string) : `preview_url`, `pdf_url` et l'`url` de chaque fichier. Le jeton EST le droit d'accès — ce ne sont pas des liens publics. Elles traversent le contexte de l'agent et tout ce qui journalise un résultat d'outil : les traiter comme un porteur de droit, pas comme une référence inerte.
- ⚠️ **`formattedAnswer` n'est pas revenu en live** (INPUT_TEXT, INPUT_EMAIL, FILE_UPLOAD), bien que le spec le documente. La lecture plausible est qu'il n'apparaît que là où `answer` n'est pas déjà lisible (une question à choix, dont l'`answer` est un id d'option) — **non vérifié**. `answers_by_title` retombe donc sur `answer`, et `formatted` peut valoir `null`.
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

## note — un 401 de Tally ne veut PAS dire « clé invalide »

c'est le piège le plus coûteux de cette API, relevé en live :
- `GET /webhooks` rend **401 tant qu'aucun webhook n'a jamais été créé** sur le compte. Après une première création il rend 200 — et continue même après les avoir tous supprimés. Le 401 dit « l'intégration webhooks n'existe pas encore », pas « ta clé est mauvaise ».
- `tally_form(op="blocks")`, `tally_form(op="update_question")` et `tally_workspace(op="create")` rendent 401 sur un plan **FREE** : c'est un gate de PLAN, pas d'authentification.

le connecteur ne traduit donc **aucun** 401 en « clé rejetée » — même sans contexte connu, le message nomme les deux causes et renvoie vers `tally_account(op="me")`, qui tranche : s'il répond, la clé est bonne et c'est le plan qui bloque.

## note — état de vérification

dérivé du spec OpenAPI 3.0.1 réel (`developers.tally.so/api-reference/openapi.json`, lu le 2026-08-31), **et testé en live le 2026-08-31** avec une vraie clé `tly-` sur un compte FREE.

**exercé en vrai, conforme au code** : `op="me"` ; le cycle complet d'un formulaire (création → lecture → renommage → publication → corbeille) ; questions ; réponses (liste + `filter=partial`) ; les cinq vues d'analytics ; le cycle complet d'un webhook (création → liste → journal d'événements → PATCH fusionné → suppression) — y compris la vérification que `op="update"` avec le seul `is_enabled=False` **n'efface pas l'URL** ; le refus d'un argument non pertinent ; et le fait que `dry_run` n'écrit rien et n'échoe jamais le `signing_secret`. Le compte a été rendu à son état initial (0 formulaire, 0 webhook).

**enveloppes relevées** (le spec ne les tranchait pas) : `/forms` et `/workspaces` rendent `{items, page, limit, total, hasMore}` ; `/webhooks` rend `{webhooks, …}` et **non** `items` ; `/forms/{id}/questions` rend `{questions, hasResponses}` ; `/organizations/{id}/users` et `.../invites` rendent un **tableau nu** ; les `PATCH`/`DELETE` de webhook rendent un **corps vide**.

**NON exercé**, faute de plan ou de matière, et donc dérivé du spec seul : lecture/écriture des blocs (401 de plan), écriture d'espaces et tout ce qui touche aux dossiers (Pro), `update_question` (un formulaire fraîchement créé ne rend aucune question, même publié), lecture et suppression d'UNE réponse (aucune réponse sur le compte), invitations (enverrait de vrais emails), retrait d'un membre (retirerait le titulaire de sa propre org et révoquerait la clé de l'appel), rejeu d'un événement (aucun événement livré).

**une question tire son intitulé du bloc `LABEL` qui la précède** : un formulaire dont les inputs n'ont pas de `LABEL` rend `{"questions": [], "hasResponses": false}` même publié — ce n'est pas un bug, c'est qu'il n'y a rien à nommer. Trouvé en live après avoir cru à un endpoint cassé.

⚠️ **`metrics` et `drop_off` ne donnent pas le même `completionRate`** sur la même période (100 vs 50 sur une visite et une soumission, mesuré) : ils ne comptent pas la même chose. Ne pas les mélanger dans un même tableau de bord sans dire lequel on cite.

**pièges de blocs** rencontrés à la création : un bloc `TITLE` ou `LABEL` ne doit pas partager son `groupUuid` avec un bloc d'input, et `TitlePayload` porte `html`, **pas** `title` (seul `FormTitlePayload` a les deux).
