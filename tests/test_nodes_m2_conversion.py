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


def _node_of(family: str, legacy_id: int) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = %s "
                 "AND (props->>'legacy_id')::bigint = %s", (family, legacy_id))
    assert len(rows) == 1, rows
    return rows[0]


def _node_of_project(project_id: int) -> dict:
    return _node_of("prj", project_id)


def _node_of_doc(doc_id: int) -> dict:
    return _node_of("doc", doc_id)


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
    from oto_mcp.db import create_doc, create_project
    from oto_mcp.db._conn import _connect

    pid = create_project("user", "sub-delivery", "Un projet", brief_md="corps")
    create_doc(pid, "Une page", body_md="corps de page")
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


# ── le point dur : poser un propriétaire sur des pages qui n'en ont jamais eu ─

def test_a_page_carries_the_owner_of_its_project(live):
    """0063-D1 : l'ownership vit sur `projects`, PAS sur `docs`. Une page n'a jamais
    eu de propriétaire — elle héritait de celui de son projet par la contrainte
    `project_id NOT NULL`. Poser ce propriétaire sur chaque page EST la conversion ;
    c'est la seule chose de tout le lot qui crée ce qui n'existait pas."""
    from oto_mcp.db import create_doc, create_project

    pid = create_project("org", "42", "Porteur", brief_md="brief")
    did = create_doc(pid, "Une page", body_md="du texte", created_by="u-1")
    live()

    n = _node_of_doc(did)
    assert (n["owner_type"], n["owner_id"]) == ("org", "42")
    assert n["kind"] == "page"
    assert n["props"]["title"] == "Une page"
    assert n["props"]["body_md"] == "du texte"
    assert n["props"]["doc_kind"] == "doc"        # la provenance est une PROPRIÉTÉ
    assert "delivery" not in n["props"]


def test_the_tree_reflects_the_old_attachment(live):
    """Une page de premier niveau se rattache au nœud du PROJET (c'est ce qui fait de
    l'épingle la racine de son sous-arbre, 0054-D5) ; une sous-page se rattache à sa
    page parente. Le rattachement cesse d'être une colonne, il devient l'arbre."""
    from oto_mcp.db import create_doc, create_project

    pid = create_project("org", "42", "Avec un arbre")
    racine = create_doc(pid, "Racine")
    enfant = create_doc(pid, "Enfant", parent_id=racine)
    live()

    projet = _node_of_project(pid)
    assert _node_of_doc(racine)["parent_id"] == projet["id"]
    assert _node_of_doc(enfant)["parent_id"] == _node_of_doc(racine)["id"]


def test_a_page_moved_in_the_tree_follows(live):
    """`move_doc` reparente ET réindexe la fratrie sans que le contenu change. La
    structure est donc réconciliée hors newer-wins — sinon l'arbre se fige au premier
    boot et diverge de la source, sans erreur."""
    from oto_mcp.db import create_doc, create_project, move_doc

    pid = create_project("org", "42", "Réorganisable")
    a = create_doc(pid, "A")
    b = create_doc(pid, "B")
    live()
    assert _node_of_doc(b)["parent_id"] == _node_of_project(pid)["id"]

    move_doc(b, a)                                 # B devient enfant de A
    live()
    assert _node_of_doc(b)["parent_id"] == _node_of_doc(a)["id"]


def test_transferring_a_project_moves_its_pages_owner(live):
    """LE piège silencieux du lot. `reparent_project` ne touche AUCUNE ligne de
    `docs` : sous newer-wins seul, `EXCLUDED.updated_at > nodes.updated_at` serait
    faux pour toutes ses pages, qui resteraient chez l'ancien propriétaire —
    indéfiniment, et sans un mot. C'est le test qui justifie que la structure ait
    son propre UPDATE."""
    from oto_mcp.db import create_doc, create_project, reparent_project
    from oto_mcp.db._conn import _connect

    pid = create_project("org", "42", "À transférer avec ses pages")
    did = create_doc(pid, "Une page qui suit")
    live()
    assert _node_of_doc(did)["owner_type"] == "org"

    reparent_project(pid, "user", "sub-repreneur", context_org_id=42)
    # L'horodatage de la page est intact : c'est TOUT le propos.
    doc_updated = _rows("SELECT updated_at FROM docs WHERE id = %s", (did,))[0]
    live()
    assert _rows("SELECT updated_at FROM docs WHERE id = %s", (did,))[0] == doc_updated

    n = _node_of_doc(did)
    assert (n["owner_type"], n["owner_id"]) == ("user", "sub-repreneur")


def test_a_deleted_page_does_not_survive_as_a_node(live):
    from oto_mcp.db import create_doc, create_project, delete_doc

    pid = create_project("org", "42", "Avec une page éphémère")
    did = create_doc(pid, "Éphémère")
    live()
    assert _node_of_doc(did)
    delete_doc(did)
    live()
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'doc' "
                 "AND (props->>'legacy_id')::bigint = %s", (did,)) == []


def test_the_public_token_travels_as_a_property(live):
    """Le partage public d'une page (une seule ligne en production) voyage tel quel.
    Le transporter est le geste évident ; savoir s'il DEVIENT un accès du modèle de
    grants se tranchera quand la chaîne sera vivante — pas sur une population de un."""
    from oto_mcp.db import create_doc, create_project, set_doc_public

    pid = create_project("org", "42", "Avec une page publique")
    did = create_doc(pid, "Publique")
    token = set_doc_public(did, True)
    live()
    assert _node_of_doc(did)["props"]["public_token"] == token


# ── idempotence : prouvée par le rejeu ───────────────────────────────────────

def test_replaying_the_migration_is_a_no_op(live):
    """« La migration est idempotente » ne se démontre pas en lisant un
    `ON CONFLICT` : ce qui compte est que le rejeu ne duplique PAS, ne réécrive
    PAS, et ne bouge PAS l'horodatage — car `updated_at` est l'arbitre du
    newer-wins, et le décaler à chaque boot ferait perdre la course à une écriture
    faite par la production pendant la fenêtre."""
    from oto_mcp.db import create_project

    from oto_mcp.db import create_doc

    pid = create_project("org", "7", "Idempotent", brief_md="stable")
    create_doc(pid, "Page stable", body_md="stable aussi")
    live()
    snapshot = ("SELECT public_id, parent_id, position, owner_type, owner_id, props, "
                "updated_at FROM nodes WHERE props ? 'legacy' ORDER BY public_id")
    before = _rows(snapshot)
    assert len(before) >= 2
    live()
    live()
    assert _rows(snapshot) == before


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


# ── le corps devient des blocs ───────────────────────────────────────────────
#
# ⚠️ **La projection a QUITTÉ le boot** (ADR 0065 lot 0, oto-backend#426) : elle est
# devenue un travail de maintenance tiré par un timer quotidien. En régime stable elle
# ne coûtait qu'une sonde, mais une ROTATION DE MARQUEUR la fait re-parser tout le
# corpus — celle de `blocks_md5` vers `blocks_md5_v2` a re-parsé 1 526 nœuds, soit
# ~19 s ajoutées à la fenêtre du healthcheck par un lot qui l'ignorait.
#
# La SÉMANTIQUE gardée ici est intacte : ce qui change, c'est le moment. Les tests
# enchaînent donc « le boot PUIS la passe » là où ils n'appelaient que le boot.

def _blocks_of(node_id: int) -> list[dict]:
    return _rows("SELECT public_id, position, type, props FROM blocks "
                 "WHERE node_id = %s ORDER BY position", (node_id,))


def _passe_de_blocs() -> int:
    """La passe de maintenance qui projette les corps en blocs."""
    from oto_mcp import maintenance
    return maintenance.blocks()["parsed"]


def test_le_boot_seul_ne_projette_plus_les_blocs(live):
    """Le contrat qui a changé, rendu explicite : `init_db` ne parse plus rien.

    Sans ce test, la sortie de la projection hors du boot ne serait attestée que par
    l'absence d'un appel — donc par rien, et un lot pourrait l'y remettre sans que
    la suite bronche."""
    from oto_mcp.db import create_project

    pid = create_project("org", "42", "Boot seul", brief_md="un\n\ndeux\n")
    live()
    nid = _node_of_project(pid)["id"]
    assert _blocks_of(nid) == [], (
        "le boot a projeté des blocs — ce travail appartient à "
        "`oto-mcp maintenance blocks` depuis l'ADR 0065")
    _passe_de_blocs()
    assert len(_blocks_of(nid)) == 2


def test_the_body_of_a_converted_page_becomes_blocks(live):
    """0054-D2/0063-D2 : le corps est une séquence de blocs stockés. Le code y est
    isolé du texte — c'est le premier bloc qu'un agent voudra remplacer sans toucher
    au reste."""
    from oto_mcp.db import create_doc, create_project

    body = "# Titre\n\nDu texte.\n\n```py\nprint(1)\n```\n"
    pid = create_project("org", "42", "Avec du corps")
    did = create_doc(pid, "Page à blocs", body_md=body)
    live(); _passe_de_blocs()

    blocks = _blocks_of(_node_of_doc(did)["id"])
    assert [b["type"] for b in blocks] == ["text", "text", "code"]
    assert [b["position"] for b in blocks] == [16, 32, 48]
    assert blocks[2]["props"]["lang"] == "py"


def test_the_blocks_rebuild_the_body_character_for_character(live):
    """L'invariant du parse, vérifié APRÈS un aller-retour en base : c'est ce qui
    rend le découpage vérifiable au lieu d'être cru. Le brief d'un projet compte
    autant qu'une page — c'est le même corps."""
    from oto_mcp.db import create_project

    brief = "Contexte\n\n- un\n- deux\n\n```sh\nls -la\n```\n\nFin.\n"
    pid = create_project("org", "42", "Brief riche", brief_md=brief)
    live(); _passe_de_blocs()
    blocks = _blocks_of(_node_of_project(pid)["id"])
    assert len(blocks) > 1
    assert "".join(b["props"]["md"] for b in blocks) == brief


def test_reparsing_is_a_no_op(live):
    """Le marqueur (`props->>'blocks_md5_v2'`) évite de reparser à chaque passe — et
    surtout de fabriquer des adresses neuves à chaque fois. Une adresse de bloc qui
    change à chaque tir de timer ne serait pas une adresse."""
    from oto_mcp.db import create_project

    pid = create_project("org", "42", "Stable", brief_md="un\n\ndeux\n")
    live(); _passe_de_blocs()
    nid = _node_of_project(pid)["id"]
    before = _blocks_of(nid)
    assert len(before) == 2
    live(); _passe_de_blocs()
    assert _blocks_of(nid) == before


def test_editing_the_body_reparses_it(live):
    """Les blocs sont une PROJECTION du corps tant que l'écriture n'est pas
    basculée : un corps édité (par la prod, sur cette même base) doit voir ses blocs
    refaits à la passe suivante, sinon la page et ses blocs divergent en silence."""
    from oto_mcp.db import create_project, update_project

    pid = create_project("org", "42", "Évolutif", brief_md="une seule ligne\n")
    live(); _passe_de_blocs()
    nid = _node_of_project(pid)["id"]
    assert len(_blocks_of(nid)) == 1

    update_project(pid, brief_md="un\n\ndeux\n\ntrois\n")
    live(); _passe_de_blocs()
    blocks = _blocks_of(nid)
    assert [b["props"]["md"] for b in blocks] == ["un\n\n", "deux\n\n", "trois\n"]


def test_an_emptied_body_leaves_no_orphan_blocks(live):
    """Le corps fait foi : le vider retire ses blocs. Les laisser derrière rendrait
    la projection fausse — et un futur lecteur servirait du texte effacé."""
    from oto_mcp.db import create_project, update_project

    pid = create_project("org", "42", "À vider", brief_md="du contenu\n")
    live(); _passe_de_blocs()
    nid = _node_of_project(pid)["id"]
    assert _blocks_of(nid)

    update_project(pid, brief_md="")
    live(); _passe_de_blocs()
    assert _blocks_of(nid) == []


def test_revisions_are_not_touched(live):
    """0063-D2 : l'historique reste un instantané sérialisé. Il ne gagne ni blocs ni
    node_id — le reconstituer par assemblage le rendrait dépendant de l'état courant
    des blocs, ce qu'une révision ne doit jamais être."""
    from oto_mcp.db import create_doc, create_project, list_doc_revisions, update_doc

    pid = create_project("org", "42", "Avec un historique")
    did = create_doc(pid, "Page", body_md="v1")
    update_doc(did, body_md="v2")
    live()
    revs = list_doc_revisions(did)
    assert [r["body_md"] for r in revs] == ["v1"]


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
