"""Conversion des projets, des pages et des tableaux en NŒUDS — lots M2 et M3.

Le lot M1 a fait des couches de contexte (`guides`) des nœuds. Celui-ci fait le
reste du contenu : les **projets** et les **pages** (blueprint ADR 0054/0063,
oto-backend#287), puis les **tableaux** (`user_datastores`, #301). C'est la
conversion structurante — celle où le modèle unique cesse d'être une idée.

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
Tant que la bascule n'est pas faite, **personne ne lit ces nœuds-là**.

**La BASCULE DE LECTURE (0063-D4) n'est PAS dans ce lot**, et ce n'est pas un renvoi
de travail : c'est une **décision** qui n'a pas été prise, mesurée ici pour que le
lot suivant parte de faits.

1. **Un nœud ne peut pas garder l'id de sa ligne legacy.** `docs.id` et
   `projects.id` sont deux séquences INDÉPENDANTES qui convergent vers UNE table :
   la page 12 et le projet 12 ne peuvent pas être tous deux `nodes.id = 12`, et les
   24 nœuds du lot M1 occupent déjà le bas de la séquence. L'id legacy vit donc dans
   `props->>'legacy_id'`. Or **les surfaces distribuent cet id** — routes du
   dashboard (`/data/:id`), `oto_doc`, `project_links.target_ref`,
   `resource_grants(resource_type='project')`, `runs.project_id`,
   `orgs.kb_project_id`, la portée d'un jeton porté (`{"projects": {"12": "read"}}`).
   Lire depuis `nodes` en gardant les surfaces à l'identique impose donc de projeter
   `props->>'legacy_id'` comme `id` — ce qui n'est plus une conversion, c'est un
   régime.
2. **La lecture ne peut pas basculer sans l'écriture.** La conversion tourne au
   BOOT : une page créée après lui n'est pas dans `nodes`. Lire `nodes` en écrivant
   `docs` servirait une page qui n'existe pas encore.
3. **L'écriture ne peut pas basculer sans les satellites.** 13 colonnes de 8 tables
   pointent `docs(id)` / `projects(id)` en clé étrangère (révisions, propositions,
   backlinks, embeddings de page et de chunk, liens de projet, journal, fichiers).
   Une page qui ne vivrait QUE dans `nodes` n'a pas de ligne `docs` : son premier
   `update_doc` violerait la FK de `doc_revisions`. Déplacer ce keying est un lot en
   soi — et il touche `doc_revisions`, dont 0063-D2 dit qu'elle ne bouge pas.

Le pan qui manque est donc : *que deviennent les satellites, et l'identifiant que
les surfaces ont déjà distribué ?* Tant que ce n'est pas tranché, projeter est ce
qu'on peut livrer qui tienne.

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


# ══ Lot M3 — le rang d'une fratrie, et les tableaux (#301) ═══════════════════

# --- M-g : les positions par INTERVALLE ---------------------------------------

# **L'écart entre deux voisins**, et le seul chiffre de tout ce module qui vienne
# d'une mesure plutôt que d'un goût. 2^16 : seize insertions successives *au même
# endroit* avant que l'intervalle ne soit épuisé, et un million de frères ne
# portent que 6,5e10 — un dix-millionième de ce qu'un BIGINT sait compter.
POSITION_GAP = 1 << 16


def midpoint(after: int | None, before: int | None) -> int | None:
    """Le rang libre entre deux voisins — `None` quand l'intervalle est ÉPUISÉ.

    **C'est la règle M-g du chantier** (blueprint `chantier-modele-contenu.md` §5),
    et elle règle une question de tarif, pas d'élégance. Le banc M0 a chiffré les
    deux gestes possibles pour ordonner une fratrie :

    - **renuméroter la fratrie entière** — le pattern hérité de `docs.position`
      (« entiers espacés, réindexés atomiquement au déplacement », 0063-D2) :
      **20 secondes** sur 45 000 frères ;
    - **insérer dans l'intervalle** entre deux voisins : **1,4 milliseconde**.

    Quatorze mille fois moins cher, et l'écart n'est pas théorique : une table du
    datastore de production porte aujourd'hui 43 584 lignes, qui deviendront autant
    de nœuds frères au lot M4. « Réindexer atomiquement » y coûterait vingt secondes
    de transaction — sur le chemin nominal d'un `data_write`.

    D'où l'inversion que ce module pose : **l'insertion dans l'intervalle est
    l'opération nominale, la réindexation devient un RATTRAPAGE** (`reindex_siblings`),
    joué le jour où l'écart ne peut plus absorber. `None` est ce jour-là.

    Fonction PURE, et ce n'est pas un hasard : le cas qui compte (l'épuisement) ne
    s'observe qu'après seize insertions au même point, ou sur deux voisins collés
    hérités d'ailleurs. Il doit se tester sans base."""
    if after is None and before is None:
        return POSITION_GAP                      # fratrie vide : le premier rang
    if before is None:
        return int(after) + POSITION_GAP         # en fin : l'écart nominal
    if after is None:
        mid = int(before) // 2                   # en tête : la moitié du premier
        return mid if mid >= 1 else None
    mid = (int(after) + int(before)) // 2
    return mid if after < mid < before else None


def _sibling_scope(parent_id: int | None, owner_type: str, owner_id: str) -> tuple[str, list]:
    """Le prédicat SQL d'une FRATRIE, et la définition qui va avec.

    Frères = même parent. **À la racine (`parent_id IS NULL`), même propriétaire** —
    parce que « tous les nœuds sans parent » n'est pas une fratrie mais la table
    entière : deux orgs qui ne se connaissent pas y partageraient un ordre, et le
    premier rattrapage renumérotererait le contenu de tout le monde."""
    if parent_id is not None:
        return "parent_id = %s", [parent_id]
    return "parent_id IS NULL AND owner_type = %s AND owner_id = %s", [owner_type, owner_id]


def reindex_siblings(conn, *, parent_id: int | None, owner_type: str,
                     owner_id: str) -> int:
    """LE RATTRAPAGE : renumérote une fratrie à l'écart nominal. Rend son cardinal.

    ⚠️ **Ceci n'est pas un chemin nominal, et le jour où il le redevient, la règle
    est perdue** (cf. `midpoint` : 20 s contre 1,4 ms). On ne l'appelle que lorsque
    l'intervalle est épuisé — jamais « pour faire propre », jamais à chaque
    déplacement. `ORDER BY position NULLS LAST, id` : un frère jamais placé (rang
    nul) passe en fin, dans l'ordre de sa création."""
    where, params = _sibling_scope(parent_id, owner_type, owner_id)
    rows = conn.execute(
        f"SELECT id FROM nodes WHERE {where} ORDER BY position NULLS LAST, id",
        tuple(params)).fetchall()
    for rank, r in enumerate(rows, start=1):
        conn.execute("UPDATE nodes SET position = %s WHERE id = %s",
                     (rank * POSITION_GAP, r["id"]))
    return len(rows)


def place_after(conn, node_id: int, *, after_id: int | None, parent_id: int | None,
                owner_type: str, owner_id: str) -> int:
    """Place `node_id` juste après son frère `after_id` (`None` = en tête).

    ⚠️ **L'ancre est un NŒUD, pas un rang**, et c'est ce qui rend le rattrapage sûr :
    une réindexation change tous les rangs de la fratrie, donc un rang capturé avant
    elle désignerait ensuite un autre endroit. En repartant de l'identité du frère,
    la seconde passe vise toujours la même place.

    Deux passes au plus : après un rattrapage, l'écart vaut `POSITION_GAP` partout,
    donc `midpoint` ne peut plus refuser."""
    where, params = _sibling_scope(parent_id, owner_type, owner_id)
    for _ in range(2):
        after = None
        if after_id is not None:
            row = conn.execute("SELECT position FROM nodes WHERE id = %s",
                               (after_id,)).fetchone()
            after = None if row is None else row["position"]
        # Le voisin de droite : le plus petit rang STRICTEMENT au-dessus de l'ancre
        # (ou le plus petit de la fratrie quand on insère en tête). `node_id` s'exclut
        # lui-même — un nœud qu'on déplace est déjà de la fratrie, et se prendre pour
        # son propre voisin le figerait sur place.
        sql = (f"SELECT min(position) AS p FROM nodes WHERE {where} AND id <> %s "
               "AND position IS NOT NULL")
        args = list(params) + [node_id]
        if after is not None:
            sql += " AND position > %s"
            args.append(after)
        before = conn.execute(sql, tuple(args)).fetchone()["p"]
        pos = midpoint(after, before)
        if pos is not None:
            conn.execute("UPDATE nodes SET position = %s WHERE id = %s", (pos, node_id))
            return pos
        reindex_siblings(conn, parent_id=parent_id, owner_type=owner_type,
                         owner_id=owner_id)
    raise RuntimeError("rang introuvable après rattrapage")        # pragma: no cover


def place_at_end(conn, node_id: int, *, parent_id: int | None, owner_type: str,
                 owner_id: str) -> int:
    """Place `node_id` en FIN de fratrie — le cas nominal d'une conversion.

    Dégénérescence de `place_after` : après le dernier frère il n'y a pas de voisin
    de droite, donc `midpoint` rend `dernier + POSITION_GAP` et aucun rang existant
    ne bouge. Un lot de conversion coûte ainsi un `UPDATE` par nœud converti, et
    zéro écriture sur ce qui était déjà là."""
    where, params = _sibling_scope(parent_id, owner_type, owner_id)
    row = conn.execute(
        f"SELECT id FROM nodes WHERE {where} AND id <> %s AND position IS NOT NULL "
        "ORDER BY position DESC LIMIT 1", tuple(list(params) + [node_id])).fetchone()
    return place_after(conn, node_id, after_id=(row["id"] if row else None),
                       parent_id=parent_id, owner_type=owner_type, owner_id=owner_id)
