"""Doc — la page markdown arborescente d'un projet (modèle produit 2026-06-27).

Un Doc appartient à un projet et **hérite de son accès** (`ownership.can_access` sur le
projet — pas d'ownership propre). Le `brief_md` du projet reste la page d'entrée ; les
Docs sont les pages, en arbre via `parent_id`. kind ∈ {doc (humain), note (agent),
source (import)}.

Package **sans surface propre** — `capabilities/__init__.py` importe `core` pour son
effet de DÉCLARATION, et c'est `core` qui tire le reste. La capacité reste UNE
(`me.doc`, `oto_doc` + `POST /api/me/docs`) : ce qui est découpé ici, ce sont les
domaines qu'elle traverse, pas la surface qu'elle sert.

| module | ce qu'il porte |
|---|---|
| `common` | le socle : droit d'accès à un projet, refus nommé, ops de lecture partagée ; la page partagée SEULE (`acces_a_la_page`) et le kind `doc` du seam `ownership` |
| `view` | la FORME servie : adresses, `rev`, projection de sortie |
| `reads` | ce qui LIT sans écrire : `list`, `search`, `get`, `backlinks` |
| `writes` | ce qui écrit l'arbre : `create`, `bulk_create`, `update`, `move`, `delete`, `set_public` |
| `patch` | l'édition d'UNE région (`patch`) et ses deux axes d'adressage |
| `history` | les versions : `revisions`, `revert` |
| `partage` | partager UNE page (#1084) : ce que `oto_resource` route pour `resource_type="doc"`, et `shared_with_me` |
| `core` | `DocInput`, le dispatcher — dont l'ORDRE des branches est un contrat — et le descripteur |
"""
