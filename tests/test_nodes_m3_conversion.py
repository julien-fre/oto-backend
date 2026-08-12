"""Lot M3 (#301) — les tableaux deviennent des nœuds, contre un vrai PostgreSQL.

Même recette qu'au lot M2, et pour la même raison : ce lot EST du SQL, et il
s'exécute au boot sur la base PARTAGÉE preprod/prod. Une relecture ne prouve ni
qu'un `ON CONFLICT` arbitre, ni qu'un rejeu est un no-op, ni qu'un `||` de JSONB
retire ce qu'on croit. Conteneur jetable, **le vrai `init_db()`**, une base peuplée
avec l'**ANCIEN** code (les fonctions de `db/datastore.py` et `db/projects.py`, que
ce lot ne touche pas), puis la migration, la vérification, et le rejeu.

Ce qui est gardé ici :

1. **le namespace devient une position** — un tableau lié par un projet se range
   sous le nœud de ce projet, sinon à la racine de son propriétaire ;
2. **le schéma de colonnes devient la dimension** (0054-D4), intact — un schéma est
   une donnée du client, pas un champ de la conversion ;
3. les trois pièges du rattachement, tous relevés sur la production : un `target_ref`
   qui n'est pas un id, un nom porté par plusieurs propriétaires, un tableau lié par
   plusieurs projets ;
4. **les LIGNES ne bougent pas**, ni le bail de la file de travail (lot M4) ;
5. la migration est **idempotente** — prouvée par un rejeu, pas par un raisonnement ;
6. **la recherche des tableaux ne casse pas** (conteneurs matchés en mémoire sur le
   nom + les labels de colonnes) et les nœuds convertis n'entrent pas dans le scope
   des guides.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_m3_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()                      # install fraîche : le schéma, tel qu'il boote
        yield init_db
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _rows(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _node_of_table(ns_id: int) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = 'tbl' "
                 "AND (props->>'legacy_id')::bigint = %s", (ns_id,))
    assert len(rows) == 1, rows
    return rows[0]


def _node_of_project(project_id: int) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = 'prj' "
                 "AND (props->>'legacy_id')::bigint = %s", (project_id,))
    assert len(rows) == 1, rows
    return rows[0]


# ── le geste du lot ──────────────────────────────────────────────────────────

def test_a_table_becomes_a_node_of_its_own_kind(live):
    """0054-D4. Le genre dit ce que l'objet EST — et il ne peut PAS se déduire de la
    dimension : 29 des 83 tableaux de production ne déclarent aucun schéma (table
    libre, colonnes découvertes des lignes). Sans `kind`, ceux-là passeraient pour
    des pages."""
    from oto_mcp.db import create_datastore_namespace

    ns = create_datastore_namespace("org", "42", "prospects")
    live()

    n = _node_of_table(ns)
    assert n["kind"] == "tableau"
    assert n["props"]["title"] == "prospects"          # le nom, devenu titre
    assert (n["owner_type"], n["owner_id"]) == ("org", "42")
    assert n["public_id"].startswith("nod_")
    assert "child_schema" not in n["props"]            # table libre : pas de dimension


def test_the_column_schema_becomes_the_dimension_untouched(live):
    """0054-D4 : le schéma d'enfants est ce que les LIGNES porteront en propriétés.

    ⚠️ Il ne passe PAS par `jsonb_strip_nulls`, qui est RÉCURSIF : un `label: null`
    déclaré par le client y perdrait sa clé, en silence. Un schéma est une donnée du
    client, pas un champ de la conversion — le test plante donc un null interne."""
    from oto_mcp.db import create_datastore_namespace, set_datastore_schema

    schema = {"fields": [{"key": "societe", "label": "Société", "role": "title"},
                         {"key": "note", "label": None, "type": "text"}]}
    ns = create_datastore_namespace("org", "42", "leads")
    set_datastore_schema(ns, schema)
    live()

    assert _node_of_table(ns)["props"]["child_schema"] == schema


def test_a_table_without_owner_never_breaks_the_boot(live):
    """`user_datastores.owner_id` est NULLABLE, `nodes.owner_id` ne l'est pas : une
    ligne orpheline (invisible de tout listing, donc de toute surface) ferait tomber
    le boot si on l'insérait. Elle est écartée, et le boot passe — zéro ligne dans ce
    cas en production, mais un boot ne doit pas dépendre de ce comptage."""
    from oto_mcp.db import create_datastore_namespace
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        orphelin = int(conn.execute(
            "INSERT INTO user_datastores (owner_type, owner_id, namespace) "
            "VALUES ('user', NULL, 'sans-proprietaire') RETURNING id").fetchone()["id"])
    temoin = create_datastore_namespace("org", "42", "avec-proprietaire")
    live()

    assert _node_of_table(temoin)                       # le boot a bien tourné
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'tbl' "
                 "AND (props->>'legacy_id')::bigint = %s", (orphelin,)) == []


# ── le namespace devient une position dans l'arbre ───────────────────────────

def test_an_unlinked_table_sits_at_the_root_of_its_owner(live):
    from oto_mcp.db import create_datastore_namespace

    ns = create_datastore_namespace("org", "42", "sans-projet")
    live()
    assert _node_of_table(ns)["parent_id"] is None


def test_a_linked_table_hangs_under_the_node_of_its_project(live):
    """« Le système de nommage namespace disparaît : un tableau est nommé par sa
    place dans l'arbre » (0054-D4). La place, c'est l'épingle qui le porte."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    pid = create_project("org", "42", "Prospection")
    ns = create_datastore_namespace("org", "42", "viviers")
    add_project_link(pid, "tableau", str(ns), label="Viviers")
    live()

    assert _node_of_table(ns)["parent_id"] == _node_of_project(pid)["id"]


def test_a_link_by_name_resolves_within_the_owner_only(live):
    """#117 : l'agent lie par NOM, le dashboard par id. Un nom ne désigne un tableau
    que CHEZ UN PROPRIÉTAIRE (l'unicité est `(owner, namespace)`, et 4 noms sont
    portés par plusieurs propriétaires en production) — résoudre globalement
    rattacherait le tableau d'autrui sous le projet de quelqu'un."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    homonyme = create_datastore_namespace("org", "77", "contacts")   # chez un AUTRE
    pid = create_project("org", "42", "Avec un lien par nom")
    ns = create_datastore_namespace("org", "42", "contacts")
    add_project_link(pid, "tableau", "contacts", label="Contacts")
    live()

    assert _node_of_table(ns)["parent_id"] == _node_of_project(pid)["id"]
    assert _node_of_table(homonyme)["parent_id"] is None             # pas touché


def test_a_non_numeric_ref_does_not_break_the_boot(live):
    """⚠️ Le piège qui aurait tué le premier boot : 14 des 65 liens `tableau` de
    production portent un nom, pas un id. Un `target_ref::bigint` rendrait
    `invalid input syntax for type bigint` — et le boot entier tomberait, pour tout
    le monde. La comparaison se fait donc en TEXTE, sans garde-fou de forme.

    Le témoin est un ref qui ne résout NULLE PART : ni id, ni nom connu."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    pid = create_project("org", "42", "Avec un lien mort")
    add_project_link(pid, "tableau", "un-nom-qui-n-existe-pas", label="Fantôme")
    ns = create_datastore_namespace("org", "42", "bien-vivant")
    live()

    assert _node_of_table(ns)                                        # le boot a tourné


def test_a_table_linked_by_two_projects_takes_the_oldest(live):
    """Un lien est N↔N, un arbre n'a qu'un parent. Le critère importe moins que sa
    STABILITÉ : le plus ancien projet ne dépend pas de l'ordre de lecture, donc
    l'arbre ne change pas d'un boot à l'autre (2 tableaux dans ce cas en production)."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    premier = create_project("org", "42", "Premier")
    second = create_project("org", "42", "Second")
    ns = create_datastore_namespace("org", "42", "partage")
    add_project_link(second, "tableau", str(ns))
    add_project_link(premier, "tableau", str(ns))
    live()

    assert _node_of_table(ns)["parent_id"] == _node_of_project(premier)["id"]
    live()
    assert _node_of_table(ns)["parent_id"] == _node_of_project(premier)["id"]


def test_the_place_does_not_transfer_the_ownership(live):
    """Un tableau rangé sous le projet d'un autre garde SON propriétaire (3 liens de
    ce genre en production). Les pages du lot M2 héritaient faute d'en avoir jamais
    eu ; un tableau en a un depuis la Phase H — le lui reprendre serait une
    régression d'accès déguisée en rangement."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    pid = create_project("org", "42", "Projet d'accueil")
    ns = create_datastore_namespace("user", "sub-invite", "mon-tableau")
    add_project_link(pid, "tableau", str(ns))
    live()

    n = _node_of_table(ns)
    assert n["parent_id"] == _node_of_project(pid)["id"]
    assert (n["owner_type"], n["owner_id"]) == ("user", "sub-invite")


def test_linking_a_table_afterwards_moves_it_in_the_tree(live):
    """⚠️ **Lier un tableau ne touche PAS `user_datastores`** — l'attache vit dans
    `project_links`. Sans réconciliation structurelle, un tableau rangé après le
    premier boot resterait à la racine pour toujours, sans un mot. C'est le pendant
    exact du piège que M2 a désamorcé sur le transfert de projet."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    pid = create_project("org", "42", "Rangement tardif")
    ns = create_datastore_namespace("org", "42", "a-ranger")
    live()
    assert _node_of_table(ns)["parent_id"] is None

    add_project_link(pid, "tableau", str(ns))
    live()
    assert _node_of_table(ns)["parent_id"] == _node_of_project(pid)["id"]


def test_a_reparented_table_takes_a_rank_in_its_new_sibling_set(live):
    """Une position ne veut rien dire hors de sa fratrie : la garder ferait arriver le
    tableau au milieu d'une fratrie qu'il n'a jamais connue. Le rang est donc annulé
    au reparentage, puis repris EN FIN de la nouvelle fratrie — dans l'intervalle,
    sans renuméroter personne (M-g)."""
    from oto_mcp.db import add_project_link, create_datastore_namespace, create_project

    pid = create_project("org", "43", "Accueil")
    aine = create_datastore_namespace("org", "43", "aine")
    add_project_link(pid, "tableau", str(aine))
    cadet = create_datastore_namespace("org", "43", "cadet")
    live()

    rang_aine = _node_of_table(aine)["position"]
    assert rang_aine is not None and _node_of_table(cadet)["parent_id"] is None

    add_project_link(pid, "tableau", str(cadet))
    live()
    n = _node_of_table(cadet)
    assert n["parent_id"] == _node_of_project(pid)["id"]
    assert n["position"] > rang_aine                     # placé APRÈS son aîné
    assert _node_of_table(aine)["position"] == rang_aine  # qui n'a pas bougé


def test_every_converted_table_gets_a_rank(live):
    """Un nœud sans rang n'a pas de place dans sa fratrie — il se lit dans l'ordre de
    la clé primaire, c'est-à-dire dans aucun ordre voulu."""
    from oto_mcp.db import create_datastore_namespace

    for nom in ("un", "deux", "trois"):
        create_datastore_namespace("org", "44", nom)
    live()
    rangs = [r["position"] for r in _rows(
        "SELECT position FROM nodes WHERE props->>'legacy' = 'tbl' "
        "AND owner_id = '44' ORDER BY position")]
    assert len(rangs) == 3
    assert all(p is not None for p in rangs)
    assert rangs == sorted(rangs) and len(set(rangs)) == 3


# ── ce que le lot NE touche pas ──────────────────────────────────────────────

def test_the_rows_do_not_move(live):
    """⚠️ `datastore_rows` est le lot M4 — le volume, en dernier, quand on a appris
    sur trois types plus simples (0063-D4). Ce lot ne convertit que les CONTENEURS :
    aucune ligne ne devient un nœud, et les lignes restent où elles sont."""
    from oto_mcp.db import create_datastore_namespace, datastore_upsert_row

    ns = create_datastore_namespace("org", "42", "avec-des-lignes")
    datastore_upsert_row(ns, "r1", {"nom": "Sylvie"})
    datastore_upsert_row(ns, "r2", {"nom": "Marc"})
    live()

    assert len(_rows("SELECT 1 FROM datastore_rows WHERE ns_id = %s", (ns,))) == 2
    # UN seul nœud pour ce tableau : le conteneur. Pas trois.
    assert len(_rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'tbl' "
                     "AND (props->>'legacy_id')::bigint = %s", (ns,))) == 1
    assert _rows("SELECT 1 FROM nodes WHERE parent_id = %s",
                 (_node_of_table(ns)["id"],)) == []


def test_the_work_queue_lease_does_not_move(live):
    """Le bail de la file de travail (ADR 0046 D) vit sur les LIGNES, et migrera avec
    elles (0063-D3). Les colonnes existent sur `nodes` depuis M2 : ce lot ne les
    renseigne pas, et surtout ne libère aucun bail en cours."""
    from oto_mcp.db import create_datastore_namespace, datastore_upsert_row
    from oto_mcp.db._conn import _connect

    ns = create_datastore_namespace("org", "42", "file-de-travail")
    datastore_upsert_row(ns, "r1", {"etat": "a-faire"})
    with _connect() as conn:
        conn.execute("UPDATE datastore_rows SET claimed_by = 'agent-1', "
                     "claimed_until = NOW() + interval '1 hour' WHERE ns_id = %s", (ns,))
    live()

    bail = _rows("SELECT claimed_by FROM datastore_rows WHERE ns_id = %s", (ns,))
    assert [r["claimed_by"] for r in bail] == ["agent-1"]
    n = _node_of_table(ns)
    assert (n["claimed_by"], n["claimed_until"]) == (None, None)


def test_a_table_node_has_no_body_and_no_blocks(live):
    """Un tableau n'a pas de corps (0054 §2 : le corps est optionnel). Conséquence
    utile et voulue : ces nœuds sortent d'eux-mêmes du parse en blocs, qui ne
    sélectionne que ce qui porte un `body_md`."""
    from oto_mcp.db import create_datastore_namespace

    ns = create_datastore_namespace("org", "42", "sans-corps")
    live()
    n = _node_of_table(ns)
    assert "body_md" not in n["props"] and "blocks_md5" not in n["props"]
    assert _rows("SELECT 1 FROM blocks WHERE node_id = %s", (n["id"],)) == []


def test_the_legacy_table_is_untouched(live):
    """Purement ADDITIF : `user_datastores` reste la source de vérité et la cible des
    écritures — la prod tourne l'ancien code sur CETTE MÊME base."""
    from oto_mcp.db import create_datastore_namespace, get_datastore_namespace_by_id

    ns = create_datastore_namespace("org", "42", "intacte")
    avant = get_datastore_namespace_by_id(ns)
    live()
    assert get_datastore_namespace_by_id(ns) == avant


# ── idempotence : prouvée par le rejeu ───────────────────────────────────────

def test_replaying_the_migration_is_a_no_op(live):
    """⚠️ **`user_datastores` n'a pas d'`updated_at`** : le newer-wins des lots M1/M2
    est impossible ici, l'arbitre est le CONTENU. Ce qui doit tenir est le même : le
    rejeu ne duplique pas, ne réécrit pas, ne bouge ni le rang ni l'horodatage."""
    from oto_mcp.db import (add_project_link, create_datastore_namespace,
                            create_project, set_datastore_schema)

    pid = create_project("org", "45", "Stable")
    ns = create_datastore_namespace("org", "45", "stable")
    set_datastore_schema(ns, {"fields": [{"key": "a", "label": "A"}]})
    add_project_link(pid, "tableau", str(ns))
    create_datastore_namespace("org", "45", "stable-a-la-racine")
    live()

    snapshot = ("SELECT public_id, parent_id, position, owner_type, owner_id, props, "
                "updated_at FROM nodes WHERE props->>'legacy' = 'tbl' ORDER BY public_id")
    before = _rows(snapshot)
    assert len(before) >= 2
    live()
    live()
    assert _rows(snapshot) == before


def test_an_edit_between_two_boots_is_caught_up(live):
    """L'autre moitié : la PROD tourne l'ancien code sur cette même base et continue
    d'écrire `user_datastores` pendant la fenêtre de promotion. Sans horodatage à
    comparer, c'est le contenu qui doit rattraper — sinon la projection ment.

    Les trois écritures possibles y passent : renommer, poser un schéma, l'effacer."""
    from oto_mcp.db import (create_datastore_namespace, rename_datastore_namespace_by_id,
                            set_datastore_schema)

    ns = create_datastore_namespace("org", "46", "avant")
    live()
    assert _node_of_table(ns)["props"]["title"] == "avant"

    rename_datastore_namespace_by_id(ns, "apres")
    set_datastore_schema(ns, {"fields": [{"key": "b"}]})
    live()
    props = _node_of_table(ns)["props"]
    assert props["title"] == "apres"
    assert props["child_schema"] == {"fields": [{"key": "b"}]}

    set_datastore_schema(ns, None)          # le schéma retiré doit DISPARAÎTRE
    live()
    assert "child_schema" not in _node_of_table(ns)["props"]


def test_a_foreign_property_survives_a_refresh(live):
    """La projection écrase ce qu'elle a écrit, pas ce qu'elle trouve : les clés
    qu'elle possède sont énumérées, le reste est laissé. C'est ce qui permettra à une
    surface d'écrire sur un nœud converti sans être défaite au boot suivant."""
    from oto_mcp.db import create_datastore_namespace, rename_datastore_namespace_by_id
    from oto_mcp.db._conn import _connect

    ns = create_datastore_namespace("org", "47", "avec-un-invite")
    live()
    nid = _node_of_table(ns)["id"]
    with _connect() as conn:
        conn.execute("UPDATE nodes SET props = props || '{\"invite\": \"x\"}'::jsonb "
                     "WHERE id = %s", (nid,))
    rename_datastore_namespace_by_id(ns, "renomme")
    live()

    props = _node_of_table(ns)["props"]
    assert props["title"] == "renomme" and props["invite"] == "x"


def test_a_deleted_table_does_not_survive_as_a_node(live):
    from oto_mcp.db import create_datastore_namespace, delete_datastore_namespace_by_id

    ns = create_datastore_namespace("org", "48", "ephemere")
    live()
    assert _node_of_table(ns)
    delete_datastore_namespace_by_id(ns)
    live()
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'tbl' "
                 "AND (props->>'legacy_id')::bigint = %s", (ns,)) == []


def test_the_purge_spares_the_other_families(live):
    """La purge des tableaux est bornée à SA famille (`tbl`) : elle n'effleure ni les
    pages, ni les projets, ni les couches de contexte du lot M1 — ni, surtout, un nœud
    NATIF, qui ne porte aucune marque `legacy`."""
    from oto_mcp.db import create_datastore_namespace, create_project, set_guide_db
    from oto_mcp.db._conn import _connect

    pid = create_project("org", "49", "Épargné")
    set_guide_db("org", "49", "un-guide", "du texte")
    create_datastore_namespace("org", "49", "un-tableau")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES ('nod_natif_m3', 'tableau', 'org', '49', "
            "        '{\"title\": \"Écrit par une surface\"}'::jsonb)")
    live()

    assert _node_of_project(pid)
    assert _rows("SELECT 1 FROM nodes WHERE public_id = 'nod_natif_m3'")
    assert len(_rows("SELECT 1 FROM nodes WHERE owner_id = '49' "
                     "AND props->>'delivery' IS NOT NULL")) == 1


# ── la recherche : ne pas la casser, et ne pas l'élargir ────────────────────

def test_searching_tables_still_works(live):
    """⚠️ L'exigence explicite de l'issue. La recherche des tableaux matche des
    CONTENEURS **en mémoire**, sur le nom du namespace et les labels de colonnes —
    elle lit `user_datastores`, que la conversion ne touche pas. On le VÉRIFIE plutôt
    que de le supposer : c'est la seule surface que ce lot pouvait casser.

    Les deux voies de match sont exercées (le nom, puis un label de colonne), sans
    quoi un `!= []` passerait sur la moitié du contrat."""
    from unittest.mock import patch

    from oto_mcp.db import create_datastore_namespace, set_datastore_schema
    from oto_mcp.search import _match_tableaux

    ns = create_datastore_namespace("org", "50", "vivier-audiens")
    set_datastore_schema(ns, {"fields": [{"key": "eff", "label": "Effectif salarié"}]})
    live()

    with patch("oto_mcp.search.ownership.active_org_principals",
               return_value=[("org", "50")]):
        par_nom = _match_tableaux("vivier-audiens", "sub-x", 50)
        par_label = _match_tableaux("Effectif salarié", "sub-x", 50)
        rien = _match_tableaux("mot-qui-ne-figure-nulle-part", "sub-x", 50)

    assert [h["ref"] for h in par_nom] == [ns]
    assert [h["ref"] for h in par_nom] == [h["ref"] for h in par_label]
    assert par_nom[0]["title"] == "vivier-audiens"
    assert rien == []              # le détecteur sait aussi ne PAS trouver


def test_a_converted_table_is_not_found_as_a_guide(live):
    """L'invariant de M2, qui vaut sans changement : la recherche des couches de
    contexte discrimine par `props->>'delivery'`. Un tableau n'en porte pas — et il
    porte en plus un `kind` à lui, donc il est deux fois dehors.

    Le contre-exemple est dans le même test : sans lui, un `== []` passerait aussi
    bien si la requête ne trouvait JAMAIS ce mot."""
    from oto_mcp.db import create_datastore_namespace, set_guide_db
    from oto_mcp.db.search import search_guides_fts

    create_datastore_namespace("org", "51", "tarabiscote-singularite")
    live()
    assert search_guides_fts("tarabiscote-singularite", 51, "sub-x") == []

    set_guide_db("org", "51", "sonde-guide", "tarabiscote-singularite")
    hits = search_guides_fts("tarabiscote-singularite", 51, "sub-x")
    assert [h["slug"] for h in hits] == ["sonde-guide"]


def test_no_converted_table_carries_a_delivery(live):
    """⚠️ Le détecteur est VALIDÉ avant d'être cru : on plante une anomalie, on
    vérifie qu'elle est vue, puis on la retire. Un test qui cherche une anomalie et
    n'en trouve pas ne prouve rien tant qu'on n'a pas vu qu'il sait en trouver une."""
    from oto_mcp.db import create_datastore_namespace
    from oto_mcp.db._conn import _connect

    create_datastore_namespace("org", "52", "une-table-de-plus")
    live()

    detect = ("SELECT public_id FROM nodes WHERE props->>'legacy' = 'tbl' "
              "AND props ? 'delivery'")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES ('nod_faux_guide_m3', 'tableau', 'org', '52', "
            "        '{\"legacy\": \"tbl\", \"legacy_id\": 999999, "
            "          \"delivery\": \"on-demand\"}'::jsonb)")
    assert [r["public_id"] for r in _rows(detect)] == ["nod_faux_guide_m3"]
    with _connect() as conn:
        conn.execute("DELETE FROM nodes WHERE public_id = 'nod_faux_guide_m3'")

    assert _rows(detect) == []


def test_datastore_surfaces_are_unchanged(live):
    """Les surfaces ne bougent pas d'un octet (0063-D4) : la conversion PROJETTE, la
    lecture n'est pas basculée. Le listing d'un propriétaire rend exactement ce qu'il
    rendait — mêmes lignes, mêmes clés."""
    from oto_mcp.db import (create_datastore_namespace, list_datastore_namespaces_for_owners,
                            set_datastore_schema)

    ns = create_datastore_namespace("org", "53", "surface")
    set_datastore_schema(ns, {"fields": [{"key": "a"}]})
    avant = list_datastore_namespaces_for_owners([("org", "53")])
    live()
    assert list_datastore_namespaces_for_owners([("org", "53")]) == avant
    assert [r["namespace"] for r in avant] == ["surface"]
