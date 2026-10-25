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
- **Le dernier auteur** est la colonne `updated_by` : c'est sur lui que s'appuiera la
  règle « droits d'une app = droits de son auteur ∩ droits de son lecteur », à
  l'appel — **pas encore appliquée** : le front ne transmet pas l'app à chaque lecture.
  Tant qu'elle ne l'est pas, les apps restent ouvertes à une population choisie
  : `oto_apps` est dans `BETA_TOOLS` (face MCP) ; la face REST n'est pas gardée,
  c'est le front qui borne qui EXÉCUTE une app.

## Révisions et conflits

`rev` est une ETag de contenu (titre, manifeste, HTML, vignette), comme celle des
pages. `update` accepte `expected_rev` et refuse en 409 `conflict` si l'app a changé.
Chaque mise à jour écrit d'abord l'état précédent dans `app_revisions` ; `restore`
remet une révision (en écrivant à son tour l'état courant).

## Liste

`op=list` prend les `project_ids` dont lister les apps (au plus 200 — ceux que
l'écran tient déjà), juge l'accès à tous en une passe (`ownership.can_access_many`)
et rend des **cartes** : jamais le HTML, qui pèse des dizaines de Ko par app.
