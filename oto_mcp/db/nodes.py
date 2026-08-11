"""Conversion des projets et des pages en NŒUDS — lot M2 du modèle de contenu.

Le lot M1 a fait des couches de contexte (`guides`) des nœuds. Celui-ci fait le
reste du contenu : les **projets** et les **pages** (blueprint ADR 0054/0063,
oto-backend#287). C'est la conversion structurante — celle où le modèle unique
cesse d'être une idée.

**Le projet devient une ÉPINGLE** (0054-D5). Il ne disparaît pas en tant que
contenu, il disparaît en tant qu'*objet* : c'est un nœud comme un autre, marqué
`props->>'pinned'`, dont le nom devient le titre, le brief devient le **corps**, et
dont les pages deviennent l'**arbre**. L'épingle donne deux choses, et rien d'autre :
la borne de localisation (« vous êtes dans *Refonte de la marque* ») et le grain du
contexte dynamique.

**Le point dur du lot est l'ownership** (0063-D1, §2 du chantier). Il vit sur
`projects` (`owner_type`/`owner_id`), **pas sur `docs`** : une page n'a jamais eu de
propriétaire, elle héritait de celui de son projet par la contrainte
`project_id NOT NULL`. La conversion, c'est exactement **poser ce propriétaire sur
chaque page** — et c'est ce couple qui interdisait d'étendre la table des pages.

## Ce que ce module fait, et ce qu'il ne fait pas

**Il PROJETTE, à chaque boot** : `projects` et `docs` restent la source de vérité et
la cible des écritures ; `nodes` en reçoit une image fidèle, rafraîchie par `_init`.
La **bascule de lecture** (0063-D4) n'est pas dans ce lot — elle demande de trancher
le sort des satellites keyés sur `docs(id)` / `projects(id)` (révisions, propositions,
backlinks, embeddings, liens, journal, fichiers, grants, runs), qui est une décision,
pas une mécanique. Tant qu'elle n'est pas prise, **personne ne lit ces nœuds-là**.

**⚠️ L'invariant que ce lot doit tenir, et que rien ne gardait avant lui** : la
recherche des couches de contexte (`db/search.py`, `db/aux_embed.py`) discrimine un
guide par `props->>'delivery' = 'on-demand'`, **pas par le `kind`** — or les contenus
convertis arrivent ici en `kind='page'` eux aussi. **Aucun nœud converti ne porte de
`delivery`**, sans quoi il se met à remonter comme un guide, dans le scope des
guides. Tenu par `tests/test_nodes_m2_conversion.py`, contre un vrai PostgreSQL.

## La forme des conversions (mêmes techniques qu'au lot M1)

Chaque famille se convertit en **trois temps**, tous rejouables :

1. **le contenu** — un `INSERT … ON CONFLICT (public_id) DO UPDATE` **newer-wins**
   (`WHERE EXCLUDED.updated_at > nodes.updated_at`) : rejouer sans écriture entre
   deux passes est intégralement no-op, et une écriture faite par la PROD pendant la
   fenêtre de promotion est rattrapée au boot suivant ;
2. **la structure** — un `UPDATE` qui réconcilie propriétaire, parent et rang
   **quoi qu'en dise `updated_at`**. Gardé par un `IS DISTINCT FROM` → no-op au
   rejeu ;
3. **la purge** — les nœuds dont la ligne legacy n'existe plus. Sans elle, un
   contenu supprimé survivrait dans `nodes` jusqu'à la fin des temps.

Tout est gardé `to_regclass` par l'appelant (docs/live-migrations.md) : après le DROP
des tables legacy, un boot reste un no-op au lieu de casser, quel que soit l'ordre
des déploiements.
"""
from __future__ import annotations

# --- Identité publique (0059-D3) ---------------------------------------------

# ⚠️ La FAMILLE est ce qui empêche deux identifiants de se recouvrir. `docs.id` et
# `projects.id` sont deux séquences INDÉPENDANTES qui convergent vers une seule
# table : sans préfixe distinct, la page 12 et le projet 12 réclameraient le même
# identifiant public et l'une écraserait l'autre au premier boot, en silence.
_FAMILY_PROJECT = "prj"
_FAMILY_DOC = "doc"


def _public_id_sql(family: str, id_expr: str) -> str:
    """Le SQL de l'identifiant public d'un nœud converti, DÉRIVÉ de sa clé legacy.

    Même raisonnement qu'au lot M1 (`db/guides.py`) : dérivé, et pas tiré au sort,
    parce que c'est ce qui rend la conversion REJOUABLE sans index supplémentaire —
    la même ligne convertie deux fois produit le MÊME identifiant, `ON CONFLICT`
    arbitre, personne ne duplique. La clé dérivée doit être IMMUABLE : un id de
    séquence l'est (un projet ne change pas d'id ; il peut changer de nom, de
    propriétaire et de place — aucun n'entre ici).

    ⚠️ Ne pas généraliser : un nœud NATIF (créé par une surface, pas converti) tire
    son identifiant au sort, comme le veut 0059-D3.
    """
    return f"'nod_' || substr(md5('{family}:' || ({id_expr})::text), 1, 24)"


# Le genre d'un projet converti — et, au lot suivant, d'une page convertie. **Une
# seule valeur pour les deux**, et ce n'est pas un raccourci : l'épingle est un FLAG
# posé sur un nœud ordinaire (0054-D5), pas un genre. Un `kind='project'`
# réintroduirait l'objet que ce lot retire.
_KIND = "page"


# --- Projets → nœuds épinglés ------------------------------------------------

# Le brief devient le CORPS (`body_md`, la même clé que les couches de contexte —
# donc indexé par la même expression de recherche, cf. `db/search.NODES_TEXT`), le
# nom devient le TITRE, et `pinned` porte l'épingle.
#
# ⚠️ Aucune clé `delivery` ici, et c'est un invariant, pas une omission : la
# recherche des guides discrimine là-dessus (cf. l'en-tête du module).
#
# `jsonb_strip_nulls` : une icône absente ne doit pas s'écrire `"icon": null` — une
# clé présente à null se lit comme une valeur, et `props ? 'icon'` répondrait vrai.
#
# NE SONT PAS PORTÉS ici, délibérément : la machinerie de PUBLICATION du projet
# (`mcp_slug`, `mcp_access`, `mcp_tools`, `mcp_expose_*`, `mcp_instructions_md`) et
# le lignage de fork (`copied_from`). Ce sont des propriétés de l'objet-projet et de
# ses surfaces, pas du contenu — elles suivront la bascule des surfaces, avec les
# questions qu'elles posent (un endpoint publié, est-ce une propriété de nœud ?).
# Rien n'est perdu : la table `projects` reste intacte.
CONVERT_PROJECTS_TO_NODES_SQL = f"""
    INSERT INTO nodes (public_id, kind, owner_type, owner_id, props,
                       created_at, updated_at)
    SELECT {_public_id_sql(_FAMILY_PROJECT, 'p.id')},
           '{_KIND}', p.owner_type, p.owner_id,
           jsonb_strip_nulls(jsonb_build_object(
               'legacy', '{_FAMILY_PROJECT}', 'legacy_id', p.id,
               'pinned', TRUE,
               'title', COALESCE(p.name, ''),
               'body_md', COALESCE(p.brief_md, ''),
               'icon', p.icon,
               'is_template', p.is_template,
               'archived_at', p.archived_at,
               'context_org_id', p.context_org_id,
               'created_by', p.created_by)),
           p.created_at, p.updated_at
      FROM projects p
    ON CONFLICT ON CONSTRAINT nodes_public_id_key DO UPDATE SET
        props = EXCLUDED.props, updated_at = EXCLUDED.updated_at
     WHERE EXCLUDED.updated_at > nodes.updated_at
"""

# Réconciliation STRUCTURELLE, hors newer-wins. Un projet épinglé est une RACINE
# (`parent_id IS NULL`) : c'est le contrat de l'épingle — on remonte jusqu'à elle,
# pas au-delà. Le propriétaire, lui, change sans que le contenu bouge
# (`reparent_project` réécrit `owner_*` et rien d'autre de substantiel), donc il ne
# peut pas dépendre d'une comparaison d'horodatage.
# Piloté par `nodes.public_id` (index d'identité), pas par un prédicat sur `props` :
# la table portera des millions de lignes en M4, ce balayage-là ne doit pas naître.
RECONCILE_PROJECT_NODES_SQL = f"""
    UPDATE nodes n
       SET owner_type = p.owner_type, owner_id = p.owner_id, parent_id = NULL
      FROM projects p
     WHERE n.public_id = {_public_id_sql(_FAMILY_PROJECT, 'p.id')}
       AND (n.owner_type, n.owner_id, n.parent_id)
           IS DISTINCT FROM (p.owner_type, p.owner_id, NULL::bigint)
"""

# Une page ou un projet supprimé laisserait sinon son nœud derrière lui pour
# toujours — et la projection cesserait d'être fidèle sans que rien ne le dise.
#
# ⚠️ **Le prédicat porte sur `props->>'legacy'`, et c'est ce qui rend la purge sûre
# le jour de la bascule d'écriture** : un nœud NATIF (créé par une surface, sans
# ligne legacy) n'a pas cette clé, donc n'est jamais candidat. Ne pas relâcher ce
# prédicat en croyant simplifier — ce serait effacer le contenu neuf.
PURGE_PROJECT_NODES_SQL = f"""
    DELETE FROM nodes n
     WHERE n.props->>'legacy' = '{_FAMILY_PROJECT}'
       AND NOT EXISTS (SELECT 1 FROM projects p
                        WHERE p.id = (n.props->>'legacy_id')::bigint)
"""


# --- Pages → nœuds ------------------------------------------------------------

# **LE POINT DUR DU LOT, et il tient en une ligne** : `p.owner_type, p.owner_id` —
# le propriétaire du PROJET, posé sur chaque page.
#
# L'ownership vit sur `projects`, **pas sur `docs`** (0063-D1, §2 du chantier) : une
# page n'a jamais eu de propriétaire, elle héritait de celui de son projet par la
# contrainte `project_id NOT NULL`. C'est ce couple qui interdisait d'étendre la
# table des pages — retirer la contrainte laisserait des pages sans propriétaire, et
# le poser sur chaque page EST la conversion. Tout le reste de ce fichier déménage
# des colonnes ; cette ligne-là crée quelque chose qui n'existait pas.
#
# `docs.kind` ('doc' humain | 'note' agent | 'source' import) devient
# `props->>'doc_kind'` : la colonne `kind` du nœud dit ce que l'objet EST (une page),
# pas d'où il vient. Les confondre rendrait la provenance structurante — et
# rappellerait le `kind='guide'` que M1 a précisément dissous.
#
# `public_token` voyage tel quel (**une seule page en porte un** en production,
# relevé du 11/08). Savoir s'il DEVIENT un accès du modèle de 0053 se tranchera
# quand la chaîne de grants sera vivante : décider maintenant, ce serait calibrer un
# modèle d'accès sur une population de un — l'erreur exacte qui a produit #282.
CONVERT_DOCS_TO_NODES_SQL = f"""
    INSERT INTO nodes (public_id, kind, owner_type, owner_id, position, props,
                       created_at, updated_at)
    SELECT {_public_id_sql(_FAMILY_DOC, 'd.id')},
           '{_KIND}', p.owner_type, p.owner_id, d.position,
           jsonb_strip_nulls(jsonb_build_object(
               'legacy', '{_FAMILY_DOC}', 'legacy_id', d.id,
               'title', COALESCE(d.title, ''),
               'description', d.description,
               'body_md', COALESCE(d.body_md, ''),
               'doc_kind', d.kind,
               'public_token', d.public_token,
               'project_id', d.project_id,
               'created_by', d.created_by)),
           d.created_at, d.updated_at
      FROM docs d JOIN projects p ON p.id = d.project_id
    ON CONFLICT ON CONSTRAINT nodes_public_id_key DO UPDATE SET
        props = EXCLUDED.props, position = EXCLUDED.position,
        updated_at = EXCLUDED.updated_at
     WHERE EXCLUDED.updated_at > nodes.updated_at
"""

# L'ARBRE, et le propriétaire hérité. Deux raisons de sortir ceci du newer-wins, et
# la seconde est un piège silencieux :
#
# 1. **le parent ne peut pas être résolu à l'insertion** — le nœud du parent peut
#    naître dans le MÊME `INSERT … SELECT`, donc n'être visible qu'après ;
# 2. **le propriétaire d'une page ne dépend pas de son horodatage** : transférer un
#    projet (`reparent_project`) ne touche AUCUNE ligne de `docs`. Sous newer-wins
#    seul, `EXCLUDED.updated_at > nodes.updated_at` serait faux pour toutes ses
#    pages — qui resteraient donc chez l'ANCIEN propriétaire, indéfiniment et sans
#    un mot. C'est précisément la classe de panne que ce lot a pour objet d'éviter.
#
# Le rattachement : le nœud de `docs.parent_id` s'il existe, sinon celui du PROJET.
# C'est ce qui fait de l'épingle la racine de son sous-arbre (0054-D5) — une page de
# premier niveau n'était rattachée à son projet que par une colonne, elle l'est
# maintenant par l'arbre.
RECONCILE_DOC_NODES_SQL = f"""
    UPDATE nodes n
       SET owner_type = p.owner_type, owner_id = p.owner_id,
           parent_id = COALESCE(par.id, prj.id)
      FROM docs d
      JOIN projects p ON p.id = d.project_id
      JOIN nodes prj ON prj.public_id = {_public_id_sql(_FAMILY_PROJECT, 'd.project_id')}
      LEFT JOIN nodes par
             ON d.parent_id IS NOT NULL
            AND par.public_id = {_public_id_sql(_FAMILY_DOC, 'd.parent_id')}
     WHERE n.public_id = {_public_id_sql(_FAMILY_DOC, 'd.id')}
       AND (n.owner_type, n.owner_id, n.parent_id)
           IS DISTINCT FROM (p.owner_type, p.owner_id, COALESCE(par.id, prj.id))
"""

PURGE_DOC_NODES_SQL = f"""
    DELETE FROM nodes n
     WHERE n.props->>'legacy' = '{_FAMILY_DOC}'
       AND NOT EXISTS (SELECT 1 FROM docs d
                        WHERE d.id = (n.props->>'legacy_id')::bigint)
"""


def convert_projects(conn) -> None:
    """Projets → nœuds épinglés : contenu, structure, purge. Rejouable."""
    conn.execute(CONVERT_PROJECTS_TO_NODES_SQL)
    conn.execute(RECONCILE_PROJECT_NODES_SQL)
    conn.execute(PURGE_PROJECT_NODES_SQL)


def convert_docs(conn) -> None:
    """Pages → nœuds : contenu, puis propriétaire hérité + arbre, puis purge.

    ⚠️ L'ORDRE avec `convert_projects` compte : le rattachement d'une page de
    premier niveau vise le nœud de son PROJET. S'il n'existe pas encore, la
    jointure ne rend rien et la page reste orpheline jusqu'au boot suivant — un
    arbre à moitié posé, qu'aucune erreur ne signale."""
    conn.execute(CONVERT_DOCS_TO_NODES_SQL)
    conn.execute(RECONCILE_DOC_NODES_SQL)
    conn.execute(PURGE_DOC_NODES_SQL)
