"""Lot M4 (#308) — les LIGNES de tableau deviennent des nœuds, contre un vrai PostgreSQL.

Même recette qu'aux lots M2/M3, et pour la même raison : ce lot EST du SQL, joué au
boot sur la base PARTAGÉE preprod/prod. Conteneur jetable, **le vrai `init_db()`**,
une base peuplée avec l'**ANCIEN** code (`db/datastore.py`, que ce lot ne touche
pas), puis la migration, la vérification, le rejeu.

Ce lot est le seul à porter du VOLUME (43 584 lignes en production, soixante fois
tout le reste du contenu réuni), et trois de ses invariants ne se retrouvent nulle
part dans les lots précédents :

1. **la clé legacy est COMPOSITE** — `datastore_rows` a pour clé primaire
   `(ns_id, row_id)` et aucune colonne `id` : la dérivation d'identité et la
   jointure de purge des trois lots précédents (`legacy_id::bigint`) n'ont ici
   aucun sens, et deux tableaux différents ne doivent pas produire le même nœud ;
2. **le bail ne bouge pas** — `claimed_by`/`claimed_until` restent lus et écrits sur
   `datastore_rows` ; la projection ne les copie même pas, et la file de travail
   (`data_claim_next`, SKIP LOCKED) traverse la conversion sans rien voir ;
3. **l'intégrité de l'arbre vit dans le CODE** — `parent_id` n'a pas de clé
   étrangère (arbitrage M-e, tranché le 12/08), donc rien ne supprime les
   nœuds-lignes quand leur tableau disparaît : la purge est le SEUL garant, et le
   cas orphelin est testé pour lui-même.

S'y ajoutent les invariants communs du patron : le rejeu est un no-op (prouvé, pas
raisonné), et la recherche des lignes ne casse pas.
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

    name = "oto_m4_" + uuid.uuid4().hex[:8]
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


def _node_of_row(ns_id: int, row_id: str) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = 'row' "
                 "AND (props->>'legacy_ns')::bigint = %s "
                 "AND props->>'legacy_row' = %s", (ns_id, row_id))
    assert len(rows) == 1, rows
    return rows[0]


def _node_of_table(ns_id: int) -> dict:
    rows = _rows("SELECT * FROM nodes WHERE props->>'legacy' = 'tbl' "
                 "AND (props->>'legacy_id')::bigint = %s", (ns_id,))
    assert len(rows) == 1, rows
    return rows[0]


def _fresh_table(owner_id: str = "42", name: str | None = None) -> int:
    from oto_mcp.db import create_datastore_namespace
    return create_datastore_namespace("org", owner_id,
                                      name or ("t-" + uuid.uuid4().hex[:8]))


# ── le geste du lot ──────────────────────────────────────────────────────────

def test_a_row_becomes_a_node_under_its_table(live):
    """0054-D4 : une ligne est un nœud-ligne, ENFANT du nœud de son tableau, et son
    propriétaire est celui du tableau — elle n'en a pas en propre."""
    from oto_mcp.db import datastore_insert_row

    ns = _fresh_table(owner_id="7")
    datastore_insert_row(ns, "r1", {"societe": "ACME", "statut": "a_qualifier"})
    live()

    n = _node_of_row(ns, "r1")
    assert n["kind"] == "ligne"
    assert n["parent_id"] == _node_of_table(ns)["id"]
    assert (n["owner_type"], n["owner_id"]) == ("org", "7")
    assert n["props"]["data"] == {"societe": "ACME", "statut": "a_qualifier"}
    assert n["public_id"].startswith("nod_")


def test_identity_derives_from_the_composite_key(live):
    """⚠️ L'invariant propre à ce lot. `datastore_rows` n'a pas de colonne `id` : son
    identité est `(ns_id, row_id)`. Deux tableaux emploient couramment le MÊME
    `row_id` (« r1 », ou le même uuid réimporté) — si l'identité ne dérivait que du
    `row_id`, la seconde ligne écraserait la première au premier boot, en silence, et
    un tableau perdrait des lignes sans qu'aucune erreur ne le signale."""
    from oto_mcp.db import datastore_insert_row

    a, b = _fresh_table(), _fresh_table()
    datastore_insert_row(a, "meme-id", {"cote": "A"})
    datastore_insert_row(b, "meme-id", {"cote": "B"})
    live()

    na, nb = _node_of_row(a, "meme-id"), _node_of_row(b, "meme-id")
    assert na["public_id"] != nb["public_id"]
    assert na["props"]["data"]["cote"] == "A"
    assert nb["props"]["data"]["cote"] == "B"


def test_rows_of_a_table_are_siblings_in_insertion_order(live):
    """L'ordre des lignes n'existe pas aujourd'hui : l'arrivée est l'ordre
    d'insertion (écart nominal, cf. le brief). Ce qui compte est qu'il soit TOTAL et
    stable — deux frères ne partagent jamais un rang, et l'ordre des rangs suit celui
    des créations."""
    from oto_mcp.db import datastore_insert_row

    ns = _fresh_table()
    for i in range(5):
        datastore_insert_row(ns, f"r{i}", {"i": i})
    live()

    places = _rows(
        "SELECT props->>'legacy_row' AS row_id, position FROM nodes "
        "WHERE props->>'legacy' = 'row' AND (props->>'legacy_ns')::bigint = %s "
        "ORDER BY position", (ns,))
    assert [p["row_id"] for p in places] == [f"r{i}" for i in range(5)]
    assert all(p["position"] is not None for p in places)
    assert len({p["position"] for p in places}) == 5      # aucun rang partagé


# ── le bail et la file de travail : intacts ──────────────────────────────────

def test_the_lease_never_moves_to_the_nodes(live):
    """0063-D3 : le bail vit sur `datastore_rows` jusqu'à la bascule de lecture. La
    projection ne le copie PAS — un bail change sans passer par un boot, donc une
    colonne projetée mentirait entre deux. Un manque est visible ; un mensonge non."""
    from oto_mcp.db import datastore_claim_next, datastore_insert_row

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"x": 1})
    pris = datastore_claim_next(ns, worker="w1")
    assert pris is not None and pris["claimed_by"] == "w1"
    live()

    # Le bail est intact côté source…
    src = _rows("SELECT claimed_by, claimed_until FROM datastore_rows "
                "WHERE ns_id = %s AND row_id = 'r1'", (ns,))[0]
    assert src["claimed_by"] == "w1" and src["claimed_until"] is not None
    # …et absent côté nœud, colonnes comme propriétés.
    n = _node_of_row(ns, "r1")
    assert n["claimed_by"] is None and n["claimed_until"] is None
    assert "claimed_by" not in n["props"] and "claimed_by" not in n["props"]["data"]


def test_the_work_queue_still_works_after_conversion(live):
    """La file traverse la conversion sans rien voir : le chemin (`SKIP LOCKED`) et
    son ordre sont ceux d'avant, et une ligne réservée avant le boot n'est pas
    reservie après."""
    from oto_mcp.db import datastore_claim_next, datastore_insert_row

    ns = _fresh_table()
    for i in range(3):
        datastore_insert_row(ns, f"r{i}", {"i": i})
    premier = datastore_claim_next(ns, worker="w1")
    live()

    suivant = datastore_claim_next(ns, worker="w2")
    assert suivant is not None
    assert suivant["row_id"] != premier["row_id"]     # pas de double service
    assert suivant["claimed_by"] == "w2"


# ── l'intégrité de l'arbre, portée par le CODE ───────────────────────────────

def test_a_deleted_row_loses_its_node(live):
    """La purge, bornée à sa famille. Sans elle, un nœud-ligne survivrait à sa
    ligne — et rien dans la base ne le supprimerait, faute de clé étrangère."""
    from oto_mcp.db import datastore_delete_row, datastore_insert_row

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"x": 1})
    datastore_insert_row(ns, "r2", {"x": 2})
    live()
    assert datastore_delete_row(ns, "r1") is True
    live()

    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'row' "
                 "AND (props->>'legacy_ns')::bigint = %s "
                 "AND props->>'legacy_row' = 'r1'", (ns,)) == []
    _node_of_row(ns, "r2")            # la voisine est intacte


def test_orphans_are_purged_when_the_whole_table_goes(live):
    """⚠️ **Le test que l'absence de clé étrangère rend obligatoire** (arbitrage M-e).
    Supprimer un tableau emporte ses lignes par CASCADE côté `datastore_rows`, mais
    RIEN n'emporte les nœuds-lignes : `parent_id` n'a pas de contrainte. Le seul
    garant est la purge — si elle rate, l'arbre garde des branches dont le tronc a
    disparu, et personne ne s'en aperçoit avant de lire l'arbre.

    Ce que la clé étrangère aurait coûté à la place (banc M0) : +36 % sur chaque
    écriture de masse, et ×118 à la suppression d'un tableau — 75 s de verrou sur un
    vivier, la cascade cherchant les enfants de chacun des 45 000 enfants."""
    from oto_mcp.db import (datastore_insert_row,
                            delete_datastore_namespace_by_id)

    ns = _fresh_table()
    for i in range(4):
        datastore_insert_row(ns, f"r{i}", {"i": i})
    live()
    assert len(_rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'row' "
                     "AND (props->>'legacy_ns')::bigint = %s", (ns,))) == 4

    assert delete_datastore_namespace_by_id(ns) is True
    live()

    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'row' "
                 "AND (props->>'legacy_ns')::bigint = %s", (ns,)) == []
    assert _rows("SELECT 1 FROM nodes WHERE props->>'legacy' = 'tbl' "
                 "AND (props->>'legacy_id')::bigint = %s", (ns,)) == []


def test_purge_stays_inside_its_own_family(live):
    """Même garde qu'aux lots précédents : la purge des lignes n'effleure ni les
    tableaux, ni les pages, ni les projets, ni un nœud NATIF (créé par une surface,
    donc sans clé legacy — jamais candidat)."""
    from oto_mcp.db import datastore_insert_row
    from oto_mcp.db._conn import _connect

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"x": 1})
    live()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES ('nod_natif_m4', 'page', 'org', '42', '{\"title\":\"natif\"}')")
    live()

    assert _rows("SELECT 1 FROM nodes WHERE public_id = 'nod_natif_m4'") != []
    _node_of_row(ns, "r1")


# ── idempotence et surfaces inchangées ───────────────────────────────────────

def test_replay_is_a_no_op(live):
    """Prouvé, pas raisonné : un second boot ne réécrit AUCUN nœud-ligne. La preuve
    est `updated_at`, que la fusion de propriétés repose à `NOW()` dès qu'elle
    écrit — un rejeu qui écrit ferait avancer l'horodatage."""
    from oto_mcp.db import datastore_insert_row

    ns = _fresh_table()
    for i in range(6):
        datastore_insert_row(ns, f"r{i}", {"i": i})
    live()
    avant = _rows("SELECT public_id, position, updated_at FROM nodes "
                  "WHERE props->>'legacy' = 'row' AND (props->>'legacy_ns')::bigint = %s "
                  "ORDER BY public_id", (ns,))
    live()
    apres = _rows("SELECT public_id, position, updated_at FROM nodes "
                  "WHERE props->>'legacy' = 'row' AND (props->>'legacy_ns')::bigint = %s "
                  "ORDER BY public_id", (ns,))

    assert avant == apres


def test_an_edited_row_is_caught_up_at_the_next_boot(live):
    """L'autre face de l'idempotence : la projection n'est fidèle que si une écriture
    faite PAR LA PROD pendant la fenêtre de promotion est rattrapée. L'arbitre est le
    CONTENU, donc une donnée modifiée est réécrite — et elle seule."""
    from oto_mcp.db import datastore_insert_row, datastore_upsert_row

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"statut": "a_qualifier"})
    datastore_insert_row(ns, "r2", {"statut": "valide"})
    live()
    fige = _node_of_row(ns, "r2")["updated_at"]

    datastore_upsert_row(ns, "r1", {"statut": "gagne"})
    live()

    assert _node_of_row(ns, "r1")["props"]["data"] == {"statut": "gagne"}
    assert _node_of_row(ns, "r2")["updated_at"] == fige      # la voisine n'a pas bougé


def test_row_search_still_finds_values_inside_rows(live):
    """La recherche des lignes reste servie par `datastore_rows` (#67 V2.1) —
    l'unification des index est le lot M5, pas celui-ci. Le test garde le fait
    utilisateur : une valeur écrite DANS une ligne reste trouvable."""
    from oto_mcp.db import datastore_insert_row
    from oto_mcp.db.search import search_datastore_rows_fts

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"societe": "Boulangerie Sylvestre"})
    live()

    trouve = search_datastore_rows_fts("Sylvestre", [ns], limit=10)
    assert any(r.get("row_id") == "r1" for r in trouve), trouve


def test_conversion_does_not_touch_the_source_rows(live):
    """La projection est en LECTURE seule sur `datastore_rows` : ni la donnée, ni les
    horodatages, ni le bail ne bougent. C'est ce qui permet à la prod de tourner
    l'ancien code sur cette même base pendant la fenêtre de promotion."""
    from oto_mcp.db import datastore_insert_row

    ns = _fresh_table()
    datastore_insert_row(ns, "r1", {"x": 1})
    avant = _rows("SELECT * FROM datastore_rows WHERE ns_id = %s", (ns,))
    live()
    assert _rows("SELECT * FROM datastore_rows WHERE ns_id = %s", (ns,)) == avant
