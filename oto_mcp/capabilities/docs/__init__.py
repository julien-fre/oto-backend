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
| `common` | le socle : droit d'accès à un projet, refus nommé, ops de lecture partagée |
| `view` | la FORME servie : adresses, `rev`, projection de sortie |
| `notify` | les emails de proposition, calculés PAR destinataire (adresse, marque, langue) |
| `reads` | ce qui LIT sans écrire : `list`, `search`, `get`, `backlinks` |
| `writes` | ce qui écrit l'arbre : `create`, `bulk_create`, `update`, `move`, `delete`, `set_public` |
| `patch` | l'édition d'UNE région (`patch`) et ses deux axes d'adressage |
| `history` | les versions : `revisions`, `revert` |
| `changes` | les propositions (« les lecteurs proposent, les auteurs valident ») |
| `core` | `DocInput`, le dispatcher — dont l'ORDRE des branches est un contrat — et le descripteur |
"""
