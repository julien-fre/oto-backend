"""Lot M2 (#287) — projets et pages deviennent des nœuds, contre un vrai PostgreSQL.

**Pourquoi une vraie base ici, alors que la convention du repo est le stub** : ce lot
EST du SQL, et il s'exécute au boot sur la base PARTAGÉE preprod/prod. Une relecture
ne prouve ni qu'un `ON CONFLICT` arbitre, ni qu'un rejeu est un no-op, ni qu'un
`jsonb_strip_nulls` retire ce qu'on croit. La recette est celle du lot M1 : conteneur
jetable, **le vrai `init_db()`**, une base peuplée avec l'**ANCIEN** code (les
fonctions de `db/projects.py`, inchangées par ce lot), puis la migration, puis la
vérification — et le rejeu.

Ce qui est gardé ici :

1. un projet converti est un **nœud épinglé** dont le corps est l'ancien brief ;
2. **aucun nœud converti ne porte de `delivery`** — l'invariant que #282 a laissé
   sans gardien : la recherche des guides discrimine là-dessus, pas sur le `kind` ;
3. **la recherche des guides ne voit pas les contenus convertis**, et la recherche
   des pages continue de fonctionner (le mode d'échec de M1 est le pire qui soit :
   un contenu listé, lisible, injecté au contexte, absent de la recherche) ;
4. la migration est **idempotente** — prouvée par un rejeu, pas par un raisonnement ;
5. le propriétaire et l'arbre se **réconcilient** hors newer-wins (un transfert de
   projet ne touche aucune ligne de `docs` : sous newer-wins seul, les pages
   resteraient chez l'ancien propriétaire, en silence).
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool.

    Base dédiée (pas la `postgres` du conteneur) : le schéma complet y est posé et
    détruit avec elle, sans interférer avec les autres tests qui partagent le
    conteneur de session."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_m2_" + uuid.uuid4().hex[:8]
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


def _node_of_project(project_id: int) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = 'prj' "
                 "AND (props->>'legacy_id')::bigint = %s", (project_id,))
    assert len(rows) == 1, rows
    return rows[0]


# ── le geste du lot ──────────────────────────────────────────────────────────

def test_a_project_becomes_a_pinned_node_whose_body_is_the_brief(live):
    """0054-D5. Le projet ne devient pas un genre à part : c'est un nœud ORDINAIRE
    (`kind='page'`) que l'épingle désigne comme repère. Son nom devient le titre,
    son brief le corps — le corps, pas une propriété annexe : c'est ce qui fait de
    la page épinglée le contexte d'accueil."""
    from oto_mcp.db import create_project

    pid = create_project("org", "42", "Refonte de la marque",
                         brief_md="# Objectif\n\nRefondre.", created_by="u-1")
    live()                                        # la migration

    n = _node_of_project(pid)
    assert n["kind"] == "page"
    assert n["props"]["pinned"] is True
    assert n["props"]["title"] == "Refonte de la marque"
    assert n["props"]["body_md"] == "# Objectif\n\nRefondre."
    assert (n["owner_type"], n["owner_id"]) == ("org", "42")
    assert n["parent_id"] is None                 # une épingle est une racine
    assert n["public_id"].startswith("nod_")


def test_no_converted_node_carries_a_delivery(live):
    """⚠️ L'invariant que #282 a laissé sans gardien, et que CE lot déclenche.

    La recherche des couches de contexte discrimine par `props->>'delivery'`, PAS
    par le `kind` — et les contenus convertis arrivent en `kind='page'` eux aussi.
    Une seule clé `delivery` posée par mégarde sur un projet, et son brief remonte
    comme un guide de plateforme.

    ⚠️ Le détecteur est VALIDÉ avant d'être cru : un test qui cherche une anomalie
    et n'en trouve pas ne prouve rien tant qu'on n'a pas vu qu'il sait en trouver
    une. On en plante donc une, on vérifie qu'elle est vue, puis on la retire."""
    from oto_mcp.db import create_project
    from oto_mcp.db._conn import _connect

    create_project("user", "sub-delivery", "Un projet", brief_md="corps")
    live()

    detect = ("SELECT public_id, props->>'delivery' AS d FROM nodes "
              "WHERE props ? 'legacy' AND props ? 'delivery'")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES ('nod_faux_guide_m2', 'page', 'org', '7', "
            "        '{\"legacy\": \"prj\", \"legacy_id\": 999999, "
            "          \"delivery\": \"on-demand\"}'::jsonb)")
    assert [r["public_id"] for r in _rows(detect)] == ["nod_faux_guide_m2"]
    with _connect() as conn:
        conn.execute("DELETE FROM nodes WHERE public_id = 'nod_faux_guide_m2'")

    assert _rows(detect) == []


def test_absent_optional_fields_are_absent_not_null(live):
    """`jsonb_strip_nulls` : une clé présente à `null` se lit comme une valeur —
    `props ? 'icon'` répondrait vrai, et une surface qui teste la présence poserait
    une icône vide. Les champs facultatifs non renseignés ne doivent pas exister."""
    from oto_mcp.db import create_project

    pid = create_project("user", "sub-nulls", "Sans icône")
    live()
    props = _node_of_project(pid)["props"]
    assert "icon" not in props
    assert "context_org_id" not in props
    assert "archived_at" not in props
    assert props["is_template"] is False          # un false n'est PAS un null


# ── idempotence : prouvée par le rejeu ───────────────────────────────────────

def test_replaying_the_migration_is_a_no_op(live):
    """« La migration est idempotente » ne se démontre pas en lisant un
    `ON CONFLICT` : ce qui compte est que le rejeu ne duplique PAS, ne réécrive
    PAS, et ne bouge PAS l'horodatage — car `updated_at` est l'arbitre du
    newer-wins, et le décaler à chaque boot ferait perdre la course à une écriture
    faite par la production pendant la fenêtre."""
    from oto_mcp.db import create_project

    create_project("org", "7", "Idempotent", brief_md="stable")
    live()
    before = _rows("SELECT public_id, props, updated_at FROM nodes "
                   "WHERE props->>'legacy' = 'prj' ORDER BY public_id")
    live()
    live()
    after = _rows("SELECT public_id, props, updated_at FROM nodes "
                  "WHERE props->>'legacy' = 'prj' ORDER BY public_id")
    assert after == before


def test_an_edit_between_two_boots_is_caught_up(live):
    """L'autre moitié du newer-wins : la PROD tourne l'ancien code sur cette même
    base et continue d'écrire `projects` pendant la fenêtre de promotion. Son
    écriture doit être rattrapée au boot suivant, sans quoi la projection ment."""
    from oto_mcp.db import create_project, update_project

    pid = create_project("org", "7", "Avant", brief_md="v1")
    live()
    update_project(pid, name="Après", brief_md="v2")
    live()
    props = _node_of_project(pid)["props"]
    assert (props["title"], props["body_md"]) == ("Après", "v2")


# ── réconciliation structurelle : ce que newer-wins ne peut pas voir ─────────

def test_a_transfer_moves_the_owner_of_the_node(live):
    """Le propriétaire est réconcilié HORS newer-wins. Ici `reparent_project` bouge
    bien `updated_at`, donc la copie suffirait — mais c'est le même UPDATE qui
    portera l'héritage des PAGES au lot suivant, où plus rien ne bouge côté `docs`.
    Le garder ici, c'est garder le mécanisme, pas seulement son résultat."""
    from oto_mcp.db import create_project, reparent_project

    pid = create_project("org", "7", "À transférer")
    live()
    assert _node_of_project(pid)["owner_type"] == "org"
    reparent_project(pid, "user", "sub-nouveau", context_org_id=7)
    live()
    n = _node_of_project(pid)
    assert (n["owner_type"], n["owner_id"]) == ("user", "sub-nouveau")


def test_a_deleted_project_does_not_survive_as_a_node(live):
    """Sans purge, un contenu supprimé resterait dans `nodes` pour toujours et la
    projection cesserait d'être fidèle sans que rien ne le dise."""
    from oto_mcp.db import create_project
    from oto_mcp.db._conn import _connect

    pid = create_project("org", "7", "Éphémère")
    live()
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'prj' "
                 "AND (props->>'legacy_id')::bigint = %s", (pid,))
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
    live()
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'prj' "
                 "AND (props->>'legacy_id')::bigint = %s", (pid,)) == []


def test_a_native_node_is_never_purged(live):
    """La purge ne vise QUE ce qui porte la marque `legacy` — c'est ce qui la rendra
    sûre le jour où les surfaces écriront des nœuds natifs. Relâcher ce prédicat
    en croyant simplifier effacerait le contenu neuf."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES ('nod_natif_m2', 'page', 'user', 'sub-natif', "
            "        '{\"title\": \"Écrit par une surface\"}'::jsonb)")
    live()
    assert _rows("SELECT 1 FROM nodes WHERE public_id = 'nod_natif_m2'")


# ── la recherche : ne pas la casser, et ne pas l'élargir ────────────────────

def test_a_converted_project_is_not_found_as_a_guide(live):
    """Le mode d'échec exact que l'issue nomme : une page convertie qui remonterait
    dans le scope des GUIDES. Le brief est indexé par la même expression que la
    prose d'un guide — seule l'absence de `delivery` l'en tient dehors.

    Le contre-exemple est dans le même test, et il est indispensable : sans lui, un
    `== []` passerait aussi bien si la requête ne trouvait JAMAIS ce mot. Le même
    mot, servi par un vrai guide, doit remonter."""
    from oto_mcp.db import create_project, set_guide_db
    from oto_mcp.db.search import search_guides_fts

    create_project("org", "7", "Sonde", brief_md="tarabiscoté singularité")
    live()
    assert search_guides_fts("tarabiscoté", 7, "sub-x") == []

    set_guide_db("org", "7", "sonde-guide", "tarabiscoté singularité")
    hits = search_guides_fts("tarabiscoté", 7, "sub-x")
    assert [h["slug"] for h in hits] == ["sonde-guide"]


def test_pages_are_still_searchable_by_their_own_path(live):
    """Ce lot n'emporte PAS la recherche des pages (#282 se décide à part) — mais il
    ne doit pas la casser non plus. Elle lit `docs`, que la conversion ne touche
    pas : on le VÉRIFIE plutôt que de le supposer."""
    from oto_mcp.db import create_doc, create_project
    from oto_mcp.db.search import search_docs_fts

    pid = create_project("org", "7", "Avec des pages")
    create_doc(pid, "Une page", body_md="anticonstitutionnellement")
    live()
    hits = search_docs_fts("anticonstitutionnellement", [pid])
    assert [h["title"] for h in hits] == ["Une page"]


def test_a_real_guide_is_still_found(live):
    """Le pendant : les couches de contexte converties au lot M1 doivent rester
    trouvables une fois que des contenus sans `delivery` peuplent la même table."""
    from oto_mcp.db import search_guides_fts, set_guide_db

    set_guide_db("org", "7", "un-howto", "abracadabrantesque", title="How-to")
    live()
    hits = search_guides_fts("abracadabrantesque", 7, "sub-x")
    assert [h["slug"] for h in hits] == ["un-howto"]
