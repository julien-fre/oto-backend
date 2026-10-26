# Apps — des écrans bâtis sur la plateforme, rangés dans un projet

Une **app** est un écran qu'un utilisateur (ou un agent, pour lui) bâtit sur ses
tableaux et ses agents : tableau de bord, file de revue, formulaire, dépôt de fichier
qui lance un agent. Capacité `me.app` : outil MCP `oto_apps`, REST `POST /api/me/apps`
(une route, un `op`, comme `/api/me/docs`).

## Ce qu'est une app

- **Un manifeste** (JSONB) : les tableaux et champs qu'elle lit, les valeurs qu'elle
  peut poser sur un champ (`set`), les colonnes qu'un formulaire peut remplir (`add`),
  les agents qu'elle lit — ou lance, avec `run: true` (et `input: true` pour leur
  passer un texte) —, `files: true` pour déposer des fichiers dans le projet.
- **Du HTML**, exécuté par le front dans un cadre isolé (origine opaque, sans réseau,
  derrière un document-garde qui interdit toute navigation sortante), qui ne parle à
  la plateforme que par le pont exposé sur `window`. Le serveur ne l'exécute jamais.
- **Une vignette** (image publiée sur le stockage média, ou couleur + emoji, et les
  réglages du dessin qui l'a produite). ⚠️ L'image doit être sous la base publique
  du stockage média de l'instance (`media_store.public_base()`) : la liste l'affiche
  hors du cadre isolé, une adresse libre ferait appeler un tiers par chaque lecteur.

Elle **appartient à un projet** et en hérite l'accès (`ownership.can_access`), comme une
page — mais ce **n'est pas une page de mémoire** : ni dans l'arbre des pages, ni dans
`oto_search`, ni dans la base de connaissance. Tables `apps` et `app_revisions`
(`db/schema/apps.py`) ; lectures et écritures dans `db/apps.py` (`db.apps.<fn>`).

## Ce que le serveur juge

- **À l'écriture** (`capabilities/apps_manifeste.py`) : la forme du manifeste (mêmes
  règles que le front), puis que chaque tableau déclaré se résout **pour l'auteur**
  dans son org active, que chaque champ déclaré existe dans son schéma, et que chaque
  agent existe dans l'org. Un manifeste qui nomme ce que l'auteur ne voit pas est
  refusé, 400 `invalid_manifest`, chaque problème listé ; `op=validate` rend la liste
  sans rien écrire.
- **Chaque tableau déclaré est rangé par son NUMÉRO** (résolu à l'écriture pour
  l'auteur) : un nom résoudrait dans l'org active de qui le lit.

## Une app ouverte — auteur ∩ lecteur (`me.app.call`)

Une app ouverte ne parle qu'à `POST /api/me/apps/{app_id}/call` (face REST seule, pas
d'outil MCP : un agent n'est jamais le lecteur d'une app). Actions : `read_rows`,
`read_aggregate`, `write_cell`, `add_row`, `start_agent`, `agent_runs`, `run_result`.

- **Droits d'une app = droits de son auteur ∩ droits de son lecteur.** L'auteur est
  `updated_by` ; il est jugé par l'ownership SEUL (numéro du tableau, niveau sur
  l'agent), jamais par sa session. Le lecteur est jugé en exécutant POUR LUI les
  capacités existantes (lignes, agrégat, écriture, `run_now`). Auteur inconnu ou qui
  a perdu l'accès : 403 `app_author_unknown` / `app_author_lost_access`.
- **Borné au manifeste, côté serveur** : projection aux champs déclarés (plus `_id`,
  `_revision`, les dates), filtre / tri / regroupement / mesure sur ces champs seuls,
  recherche plein texte seulement sans liste de champs, valeurs posables, colonnes
  d'ajout, agents lisibles ou lançables (`run: true`), entrée d'au plus 300
  caractères marquée comme venue de l'app. Une écriture exige le `_revision` montré.
- **Résultats d'agent** : `agent_runs` et `run_result` rendent statut, dates, erreur
  et ce que l'agent a conclu (`runs.outcome`, `runs.note`, tronquée à 4 000
  caractères) — pour un agent déclaré, et un travail qui est un run de cet agent.
- **Ce qui n'y est pas** : les lignes changées PAR un run (le journal des révisions
  porte le `run_id`, mais sans index sur cette colonne ; à poser en index concurrent
  avant de le servir).

L'outil MCP `oto_apps` reste dans `BETA_TOOLS` ; la face REST des apps n'est pas
gardée, le front borne qui ouvre une app.

## Révisions et conflits

`rev` est une ETag de contenu (titre, manifeste, HTML, vignette), comme celle des
pages. `update` accepte `expected_rev` et refuse en 409 `conflict` si l'app a changé.
Chaque mise à jour écrit d'abord l'état précédent dans `app_revisions` ; `restore`
remet une révision (en écrivant à son tour l'état courant).

## Liste

`op=list` prend les `project_ids` dont lister les apps (au plus 200 — ceux que
l'écran tient déjà), juge l'accès à tous en une passe (`ownership.can_access_many`)
et rend des **cartes** : jamais le HTML, qui pèse des dizaines de Ko par app.
