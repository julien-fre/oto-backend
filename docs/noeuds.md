---
title: Le nouvel univers de contenu — nœuds
type: explanation
description: >-
  Le modèle de contenu unique (page, tableau, ligne) et sa surface propre, qui vit
  À CÔTÉ de l'ancienne sans la traduire. Ce que porte `props` et ce que porte `data`,
  pourquoi la recopie au démarrage est arrêtée depuis le 2026-09-01, ce qu'il reste
  du résidu qu'elle a laissé, et ce qui n'est pas encore porté (file de travail,
  filtre et tri sur un tableau natif). À lire avant de toucher `db/nodes.py`,
  `db/node_tables.py` ou une capacité `node_*`.
---

# Le nouvel univers de contenu

## Trois genres, et rien d'autre

Un nœud est **une page, un tableau ou une ligne** (ADR 0054-D5). Projet, guide et
procédure **ne sont pas des genres** : ce sont des rôles portés en propriété par une
page. Le genre dit ce que l'objet EST ; ce qu'il JOUE est une propriété, jamais un
`kind` de plus.

Une ligne est un nœud comme les autres : elle a un genre, un parent (son tableau) et
une place dans la fratrie. C'est pourquoi les **mêmes quatre verbes** — `create`,
`update`, `move`, `delete` — servent les trois genres. Leur donner chacun le sien
créerait trois vocabulaires pour une seule notion, et trois endroits où
l'autorisation, l'ordre et le refus d'écrire une copie devraient rester d'accord.

## Deux colonnes, deux natures

| colonne | ce qu'elle porte | qui l'interprète |
|---|---|---|
| `props` | titre, épingle, livraison, schéma d'enfants | **oto** |
| `data` | les valeurs des colonnes d'une ligne | **personne** |

⚠️ **Ce n'est pas du rangement.** Mêlées dans `props`, une cellule nommée `title` ou
`position` écrase le sens du nœud, et toute lecture doit connaître la liste des clés
réservées pour faire le tri. La frontière est celle du datastore — *oto gère les types
standards, jamais l'interprétation métier d'une valeur*.

Coût de la séparation, **mesuré** au banc du 2026-09-01 (200 000 lignes-tableau de six
champs, deux passes en ordre inversé) : **+4,7 %** de volume, et **−14 %** de temps
d'écriture, parce que séparer évite la concaténation jsonb qu'imposait le mélange.

⚠️ Le schéma de colonnes se stocke sous la clé **`fields`**, pas `columns` : c'est ce
que la face de lecture lit. La surface, elle, dit « colonnes » — c'est le mot du
contrat front, et la traduction se fait à un seul endroit, dans `db/node_tables.py`.
Se tromper produit un tableau qui s'affiche **sans aucune colonne, sans erreur**.

## Deux univers CÔTE À CÔTE, aucun ne traduit l'autre

Arbitrage du 2026-08-31 : *« on ne migre pas, on arrête la recopie ; la surface nœud
vit à côté de docs et part de vide »*.

- L'**ancien** monde — `oto_doc`, `oto_project`, `data_*` et leurs tables — continue de
  servir son contenu, sans rien savoir du nouveau.
- Le **nouveau** — `oto_node`, `oto_node_rows`, `oto_node_edit` — naît vide et ne se
  remplit que par ses propres verbes.

Rien ne traduit l'un vers l'autre. Un contenu créé dans l'ancien monde **n'apparaît
pas** dans le nouveau, et c'est le comportement voulu, pas une régression.

## Qui voit ces verbes

Les trois verbes MCP — `oto_node`, `oto_node_rows`, `oto_node_edit` — sont réservés aux
comptes **bêta** depuis le 2026-09-01 : un admin pose l'option `beta` sur l'utilisateur
ou sur son org, sinon ils sont masqués. Ils étaient jusque-là exposés à tout le monde
sans aucun gate — l'inverse de ce qu'on croyait, et zéro appel MCP en 30 jours explique
que personne ne l'ait remarqué.

Motif : la surface part de vide et son contrat est provisoire. La proposer à tous, c'est
offrir à chaque agent une lecture qui ne trouve rien et une écriture dont l'utilisateur
ignore la destination. Détail du grain, du fail-closed et de ses limites :
`docs/tool-visibility.md`.

⚠️ **La face REST n'est pas gatée** — le dashboard qui construit ce nouvel univers la
consomme aujourd'hui. Écart assumé, refermé quand la surface cessera d'être provisoire.

## La recopie, et pourquoi elle s'est arrêtée

Jusqu'au 2026-09-01, **cinq conversions** tournaient à chaque démarrage — projets,
pages, procédures, tableaux, lignes — et déposaient dans `nodes` une image des tables
historiques, chaque copie marquée `props.legacy`. Elles préparaient une bascule de
lecture : l'ancienne surface devait finir par lire le nouveau stockage.

Ce plan est abandonné, donc la recopie n'a plus d'objet. Elle est retirée de
`db/_init.py`, et un garde-fou (`tests/test_recopie_arretee.py`) lit l'**AST** du
module de démarrage pour qu'aucune ne revienne : un module qui expliquerait longuement
avoir cessé de recopier tout en gardant l'appel échoue.

**Une seule projection survit** : celle des couches de contexte. `db/guides.py` écrit
ses cinq gestes directement dans `nodes`, mais la table `guides` garde un écrivain — le
seed du readme plateforme — et cette projection est le seul chemin par lequel il
atteint `nodes` sur une base **neuve**. Elle partira quand le seed sèmera nativement ;
la retirer avant, c'est retirer sans remplaçant. Un contre-test l'exige.

## Le résidu, et comment il se retire

Ce que la dernière passe a laissé : **70 876 nœuds sur 70 927** et **29 174 blocs**
(mesuré le 2026-09-01) ; les ~51 nœuds restants sont les couches de contexte, natives.

Le retrait est le travail de maintenance **`residu-projete`** (`oto-mcp maintenance`).
Trois choses le gouvernent :

- **hors du boot** — son coût suit la taille de la base, la fenêtre du healthcheck est
  finie (ADR 0065) ;
- **à blanc par défaut** — c'est un acte, pas une routine : il n'est dans aucun timer
  ni dans la passe quotidienne, et `--apply` seul écrit ;
- **le compte est un DIFFÉRENTIEL d'inventaire**, jamais la réponse du geste. Un
  `DELETE` qui ne trouve rien annonce « zéro ligne » exactement comme un `DELETE` qui
  vient de tout prendre.

⚠️ **Les blocs partent AVANT leur nœud.** `blocks.node_id` ne porte aucune clé
étrangère — aucune ne pointe `nodes` — donc rien ne cascade : le nœud supprimé en
premier, plus rien ne relierait ses blocs à quoi que ce soit.

Ce qui pend à un nœud a été **mesuré** avant d'écrire le retrait : aucune clé
étrangère, aucun partage ne désigne un nœud, les 22 embeddings de nœuds sont tous
natifs, et aucun nœud natif n'a pour parent un nœud recopié.

La recherche ne perd rien : elle indexe `docs`, `projects` et `datastore_rows` **en
propre**, et ne lit `nodes` que pour les couches de contexte.

## Ce que le retrait emporte, et qui doit être décidé avant

Trois surfaces lisent aujourd'hui le résidu et deviendront vides sans lui :

1. **le contrat du dashboard** — ouvrir par la surface nœud un contenu créé dans
   l'ancienne. Trois tests sont marqués en échec **attendu strict** : s'ils repassent
   au vert, c'est qu'une recopie est revenue ;
2. **`oto_node_rows` sur un tableau recopié** — il résout son namespace par
   `props.legacy_id` ;
3. **la référence de procédure** (`node_procedure_ref`), qui vise la famille produite
   par la conversion.

⚠️ **L'identifiant dérivé et les poignées `doc_id`/`project_id` sont SERVIS** au
dashboard (`db/shell.py`), qui est le premier consommateur de ce nouvel univers. Les
retirer est un **changement de contrat**, pas un déblaiement de fin de chantier : cela
ne se décide pas ici.

⚠️ **Vocabulaire.** Les tests parlent de « front tiers » : c'est un héritage, pas une
description. Le consommateur de cette surface est **le nouveau dashboard produit**, et
c'est pour lui qu'elle est construite — pas pour un intégrateur extérieur. Lire « tiers »
comme « partenaire externe » fait surestimer le coût d'un changement de contrat.

## Ce qui n'est pas encore porté

- **La file de travail.** `nodes` porte désormais les **cinq** colonnes de bail
  (`claimed_by`, `claimed_until`, `claimed_run`, `claims`, `abandon_reason`), mais le
  chemin de réservation lit encore `datastore_rows`. ⚠️ **L'index du bail n'est pas
  posé**, et c'est délibéré : un index sur un prédicat que personne n'interroge est un
  coût d'écriture pur, et sa forme utile dépend d'un arbitrage — toute forme indexable
  en partiel change l'ordre observable de la file. Un test vérifie qu'il n'est pas là.
- **Filtre, recherche et tri sur un tableau natif.** Ils sont **refusés, pas ignorés** :
  les servir demanderait de fouiller la donnée métier, donc de l'interpréter. Les
  accepter en silence ferait croire à un filtre appliqué sur une page complète.
  ⚠️ **Et depuis le 01/09 (#621), le chemin RECOPIÉ refuse pour la même raison** une
  entrée de `filter` sans `:` (`400 invalid_filter`) : elle était ignorée, et la page
  repartait non filtrée sans le dire — le geste que l'alinéa ci-dessus interdit,
  commis sur l'autre provenance. Un curseur illisible y rend `400 invalid_cursor` (il
  sortait en 500), et sous le MÊME code que le chemin natif : la provenance d'un
  tableau n'est pas servie, un front ne peut donc pas prévoir lequel des deux il aura.

## Où vit quoi

| module | rôle |
|---|---|
| `db/schema/nodes.py` | le DDL : la table, ses colonnes, ses **deux** index de requête |
| `db/nodes.py` | pages natives, position dans la fratrie, retrait du résidu |
| `db/node_tables.py` | tableaux et lignes **natifs** — écriture et pagination |
| `db/node_view.py` | ouvrir UN nœud |
| `db/blocks.py` | le corps d'une page, en blocs à identité stable |
| `db/shell.py` | le rail de navigation servi au front |
| `capabilities/node_*.py` | les surfaces MCP + REST |
| `maintenance.residu_projete` | le retrait du résidu |
