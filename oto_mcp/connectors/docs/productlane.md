## prerequisite — clé API v2 productlane

crée une clé dans Productlane (Settings → API), puis colle-la dans oto.
- ⚠️ **une clé v1 ne marche pas ici.** L'API v2 est une API distincte, avec ses propres clés ; v1 s'arrête le **20/11/2026**. Si la clé est refusée en 401 alors qu'elle « marche ailleurs », c'est presque toujours ça
- byo-only : pas de clé oto partagée — ce sont les conversations de tes clients, chaque organisation pose la sienne
- la clé porte des **scopes** (`threads:read`, `threads:write`, `contacts:*`, `companies:*`, `projects:*`, `issues:*`, `changelogs:*`, `docs:*`, `tags:*`, `snippets:*`, `portal:read`) : un refus **403** veut dire qu'il en manque un, pas que la clé est mauvaise
- certaines familles demandent un **plan** : les extraits de réponse et la diffusion de changelog exigent Pro, les étiquettes de changelog et le portail client exigent Scale
- ⚠️ **Linear doit être connecté côté Productlane** pour tout ce qui touche à la roadmap (projets, issues, liens depuis un fil) : ce n'est pas une roadmap autonome

## usage — la boîte de retours clients, et ce qu'on en fait

- « qu'est-ce que nos clients demandent ? » → `productlane_threads(op="search", status="open")`, ou `productlane_roadmap(op="projects", sort="total_score")` pour classer par poids des retours rattachés plutôt que par date
- « lire une conversation en entier » → `productlane_threads(op="get", thread_id="…", expand=["messages","comments"])`
- « répondre au client » → `productlane_threads(op="send", thread_id="…", content="…")` — ⚠️ **cela part vraiment**, par le canal d'où vient le fil
- « laisser une note à l'équipe » → `productlane_threads(op="comment", …)` : visible des coéquipiers seulement, rien ne sort
- « rattacher ce retour à la roadmap » → `productlane_threads(op="link", thread_id="…", issue_ids=[…])` : c'est ce geste qui fait monter le score d'un projet
- « qui a demandé ça ? » → `productlane_contacts(op="search", …)` puis `op="issues"` / `op="projects"` sur le contact
- « publier une note de version » → `productlane_changelogs(op="create", fields={…})`, puis `op="update"` avec `{"published": true}` pour la rendre visible
- « prévenir les abonnés » → `productlane_changelogs(op="broadcast", changelog_id="…", email=true, dry_run=false)`
- « ce que dit notre aide en ligne » → `productlane_docs(op="articles", title_contains="…")`
- vérifier ce que la clé a le droit de faire, avant de buter dessus → `productlane_workspace(op="me")` : il rend les scopes accordés et ne demande aucun droit

## note — publier n'est pas diffuser

⚠️ **`op="broadcast"` n'a AUCUN effet sur `published`** — c'est écrit noir sur blanc côté éditeur. Les deux gestes sont indépendants, et les confondre coûte cher dans les deux sens :
- diffuser un changelog **non publié** envoie à tes abonnés un lien vers une page invisible
- publier sans diffuser ne prévient personne

publier = `productlane_changelogs(op="update", fields={"published": true})`. diffuser = `op="broadcast"`, qui est en **dry-run par défaut** : il faut `dry_run=false` pour que quelque chose parte, et il n'y a ni annulation ni rappel.

## note — la roadmap est un miroir de Linear

⚠️ **une écriture peut réussir ici pendant que la synchro Linear échoue** : l'éditeur la journalise de son côté et **ne la remonte pas dans la réponse**. Un succès sur `update_project` / `update_issue` ne prouve donc pas que Linear a suivi.
- la **création** part de Linear (l'issue y est déposée d'abord) : sans Linear connecté, elle échoue franchement
- `team_id`, `state_id`, `assignee_id`, `linear_status_id` sont des identifiants **Linear** — les lire par `productlane_roadmap(op="workflows", team_id="…")` et `op="statuses"`, jamais les coder en dur
- sur une issue, `status` n'est pas une énumération fixe : ce sont les workflow states de l'équipe Linear, propres à chaque espace de travail
- `priority` suit la numérotation Linear : **`0` = aucune priorité, `1` = urgente**, puis 2, 3, 4 par urgence décroissante. Ce n'est pas une échelle croissante

## note — trois autres pièges

- ⚠️ **`productlane_contacts(op="block", block_type="DOMAIN")` coupe toute une organisation** d'un seul appel, et l'expéditeur n'en est pas informé. `"EMAIL"` ne vise qu'une adresse
- ⚠️ **`productlane_companies(op="merge")` est irréversible, et le sens compte** : l'entreprise `company_id` survit, celle de `source_id` est supprimée
- ⚠️ **`productlane_docs(op="accept")` peut répondre `superseded`** au lieu de `accepted` : le brouillon ne s'applique plus proprement, l'article ayant bougé sous lui. C'est un succès HTTP qui n'a **rien appliqué** — lire le statut rendu, pas seulement l'absence d'erreur
