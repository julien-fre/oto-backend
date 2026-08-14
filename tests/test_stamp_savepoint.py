"""#333 — l'échec du stamp ne doit jamais emporter l'écriture métier.

Le stamp du vecteur de rang (#318) s'exécute DANS la transaction de l'écriture
qui vient de modifier la ligne, et son erreur est avalée — best-effort assumé.
Mais avaler l'exception ne répare pas la transaction : PostgreSQL l'a AVORTÉE,
et le COMMIT de sortie devient un ROLLBACK silencieux. L'écriture métier
s'évapore pendant que l'appelant reçoit l'écho du RETURNING, rendu avant
l'avortement : la fonction répond « écrit » pour une ligne qui n'existera
jamais.

Banc : la condition réelle de #333 — la colonne du vecteur ABSENTE de
`datastore_rows` (un boot dont l'ALTER n'est pas passé ; c'est ce que le banc
du module #332 déclenchait sans le vouloir). Chaque écriture métier fait alors
échouer le stamp ; sans savepoint, tout tombe. La vérité se lit par une
connexion FRAÎCHE (`datastore_get_row`) — jamais par l'écho, c'est lui qui
ment.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def sans_colonne_vecteur(pg_dsn):
    """Une base bootée par le VRAI `init_db`, puis amputée de `search_vec` sur
    `datastore_rows` — le stamp y échoue à chaque écriture, comme dans #333."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_stamp_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        from oto_mcp.db._conn import _connect
        with _connect() as conn:
            conn.execute("ALTER TABLE datastore_rows DROP COLUMN search_vec")
            ns = conn.execute(
                "INSERT INTO user_datastores (owner_type, owner_id, namespace) "
                "VALUES ('user', 'banc', 'banc_333') RETURNING id").fetchone()
        yield int(ns["id"])
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def test_linsert_persiste_malgre_lechec_du_stamp(sans_colonne_vecteur):
    from oto_mcp.db.datastore import datastore_get_row, datastore_insert_row

    ns_id = sans_colonne_vecteur
    echo = datastore_insert_row(ns_id, "r1", {"nom": "durand"})
    assert echo["data"] == {"nom": "durand"}, "l'écho, lui, a toujours dit vrai"

    relu = datastore_get_row(ns_id, "r1")
    assert relu is not None, \
        "la ligne annoncée écrite n'existe pas : le COMMIT était un ROLLBACK (#333)"
    assert relu["data"] == {"nom": "durand"}


def test_lupdate_persiste_et_lecho_dit_vrai(sans_colonne_vecteur):
    from oto_mcp.db.datastore import (datastore_get_row, datastore_insert_row,
                                      datastore_update_row)

    ns_id = sans_colonne_vecteur
    datastore_insert_row(ns_id, "r2", {"etat": "avant"})
    echo = datastore_update_row(ns_id, "r2", {"etat": "apres"},
                                "2026-08-14T00:00:00Z")
    assert echo is not None and echo["data"] == {"etat": "apres"}

    relu = datastore_get_row(ns_id, "r2")
    assert relu["data"] == {"etat": "apres"}, \
        "l'update annoncé a été roulé en arrière par l'échec du stamp (#333)"


def test_lupsert_persiste_malgre_lechec_du_stamp(sans_colonne_vecteur):
    from oto_mcp.db.datastore import datastore_get_row, datastore_upsert_row

    ns_id = sans_colonne_vecteur
    _, inserted = datastore_upsert_row(ns_id, "r3", {"v": 1})
    assert inserted is True
    relu = datastore_get_row(ns_id, "r3")
    assert relu is not None and relu["data"] == {"v": 1}
