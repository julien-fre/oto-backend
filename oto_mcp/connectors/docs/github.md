## prerequisite — jeton github

crée un jeton dans GitHub (Settings → Developer settings → Personal access tokens), puis colle-le dans oto.
- deux familles de jetons, et elles ne se règlent pas pareil :
  - **classique** — coche des *scopes* : `repo` (dépôts privés, issues, PR), `read:org` (organisations et équipes), `workflow` (GitHub Actions)
  - **fine-grained** — coche des *permissions* (Contents, Issues, Pull requests, Actions, Members…) **et la liste des dépôts** auxquels le jeton s'applique. Un dépôt absent de cette liste est invisible, même avec la bonne permission
- byo-only : pas de clé oto partagée — un jeton GitHub porte l'identité de son porteur, et chaque commit, commentaire ou fusion lui est **attribué**
- champ **URL de l'API** à laisser vide pour github.com. Pour un **GitHub Enterprise Server** auto-hébergé : `https://<votre-hôte>/api/v3`
- le bouton « tester la connexion » vérifie que le jeton est vivant et dit à quel compte il appartient. Il ne peut pas vérifier plus : les scopes d'un jeton classique ne sont lisibles que dans un en-tête de réponse, et un jeton fine-grained n'expose pas sa liste de dépôts

## usage — lire un dépôt, suivre les tickets, surveiller la CI

- « de quoi parle ce dépôt ? » → `github_repos(op="get")` puis `github_files(op="readme")`
- « lis-moi ce fichier » → `github_files(op="read", path="src/x.py")` — le contenu revient décodé
- « qu'est-ce qui a changé entre ces deux versions ? » → `github_repos(op="compare", base="v1.2.0", head="main")`
- « les tickets ouverts » → `github_issues(op="search", state="open")`
- « ouvre un ticket » → `github_issues(op="create", fields={"title": "…", "body": "…"})`
- « les PR en attente de revue » → `github_pulls(op="search", state="open")` puis `op="reviews"` sur un numéro
- « qu'est-ce que cette PR change ? » → `github_pulls(op="files", number=…)`
- « pourquoi la CI a échoué ? » → `github_actions(op="runs", status="failure")` → `op="jobs"` → `op="logs"`
- « qui a accès à ce dépôt ? » → `github_orgs(op="collaborators")`, et `op="permission"` pour le niveau EFFECTIF d'une personne (héritages d'équipe compris)
- « trouve où ce symbole est utilisé » → `github_search(op="code", q="maFonction repo:org/dépôt")`
- diagnostiquer un refus avant d'accuser un nom → `github_orgs(op="rate_limit")` (ne consomme pas de quota) et `op="me"`

## note — le piège du 404

⚠️ **sur une ressource privée que le jeton n'a pas le droit de voir, GitHub répond 404, pas 403** — exprès, pour ne pas divulguer son existence.

« dépôt introuvable » veut donc dire, dans l'ordre de probabilité : le jeton n'a pas le scope `repo` ; ou (fine-grained) ce dépôt n'est pas dans la liste du jeton ; ou l'organisation impose une autorisation SSO que le jeton n'a pas encore reçue. Vérifier le nom vient en dernier.

## note — une pull request EST une issue

⚠️ chez GitHub, les deux partagent la même numérotation et le même endpoint de liste. Conséquences :
- `github_issues(op="search")` **écarte les PR par défaut**, sans quoi « combien de tickets ouverts ? » donne un nombre faux, souvent de beaucoup. `include_pull_requests=true` rend la réponse brute de l'API
- ce tri se fait après pagination : une page de 30 dont 12 sont des PR en rend 18 — c'est normal
- réciproquement, et c'est pratique : commentaires de fil, étiquettes, assignations et jalons d'une PR passent par `github_issues` avec son numéro
- pour compter proprement des deux côtés : `github_search(op="issues", q="repo:org/dépôt is:issue is:open")`

## note — ce qui écrit, et ce qui coûte

- ⚠️ **`github_actions(op="dispatch")` déclenche une exécution réelle** — donc potentiellement un build, une publication ou un **déploiement**. Il est en **dry-run par défaut** : `dry_run=false` pour déclencher. Le workflow doit déclarer `workflow_dispatch`, sinon 404 (« pas déclenchable », pas « n'existe pas »), et la réponse ne rend pas l'exécution créée : la retrouver en listant les runs juste après
- ⚠️ **`github_pulls(op="merge")` écrit sur la branche cible**, sans annulation d'un clic. `merge` ajoute un commit de fusion, `squash` écrase la branche en un seul commit, `rebase` réécrit les commits — trois effets différents sur l'historique. Passer `sha` protège de la course : si la tête a bougé depuis la lecture, GitHub refuse au lieu de fusionner autre chose
- ⚠️ **une revue sans `event` reste en attente** (`PENDING`) : rien n'est publié, personne n'est notifié, et elle n'est visible que de son auteur. `APPROVE` peut débloquer une fusion protégée — c'est un acte de gouvernance
- ⚠️ **`github_files(op="write")` sur un fichier existant exige son `sha`** (lu par `op="list"`) : sans lui, 409. C'est le contrôle de concurrence de GitHub, qui garantit qu'on remplace bien la version lue. Chaque écriture est un **commit réel**, attribué au porteur du jeton
- ⚠️ **`github_issues(op="assign")` ignore en silence** un compte sans accès en écriture au dépôt : la réponse revient en succès sans l'avoir assigné. Comparer la liste rendue à celle demandée

## note — trois appartenances qu'on confond

⚠️ membre d'une **organisation**, membre d'une **équipe**, collaborateur d'un **dépôt** : retirer quelqu'un de l'une ne le retire pas des autres.
- `github_orgs(op="remove_member")` sort de l'organisation **et de toutes ses équipes**
- `op="remove_team_member"` ne touche que l'équipe
- `op="remove_collaborator"` ne touche qu'un dépôt, **et ne retire pas un accès hérité d'une équipe** — vérifier ensuite avec `op="permission"`

⚠️ `op="members"` ne montre que ce que le jeton a le droit de voir : sans `read:org`, seuls les membres **publics** sortent — une liste plus courte, sans erreur. Ce n'est pas un recensement. Et `op="set_membership"` / `op="add_collaborator"` **envoient une invitation** : l'accès n'est effectif qu'une fois acceptée.

## note — les bornes qui tronquent en silence

- ⚠️ **`per_page` plafonne à 100 et GitHub rabote sans erreur** au-delà. Le connecteur refuse localement en nommant la borne, plutôt que de rendre 100 lignes là où tu en croyais 500 — pour aller plus loin, paginer avec `page`
- ⚠️ **la recherche s'arrête à 1 000 résultats**, quoi qu'annonce `total_count` (qui est une estimation du corpus, pas un nombre de lignes récupérables). `github_search` remonte donc un bloc `troncature`, qui vaut vrai aussi quand GitHub a abandonné la recherche en route
- ⚠️ **la recherche de code n'indexe que la branche par défaut**, ignore les fichiers de plus de 384 Ko, et exige un vrai terme — `repo:x` seul ne suffit pas. Elle ne rend pas le contenu : le lire ensuite avec `github_files(op="read")`
- `github_pulls(op="files")` est plafonné à 3 000 fichiers (et omet les gros `patch`), `op="commits"` à 250 ; `github_repos(op="commit")` à 300 fichiers. Une PR ou un commit massif est rendu incomplet, sans erreur
