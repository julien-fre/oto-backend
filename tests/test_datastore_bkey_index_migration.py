"""L'index d'unicité de clé métier devient polymorphe, sans trou (#318).

Un index d'expression posé sur `data->>clé` compare l'OBJET dès que la colonne porte
des sous-champs : deux lignes du même SIREN, l'une nue l'autre enveloppée, ne
collisionneraient pas — doublon silencieux, alors que c'est précisément ce que
l'index existe pour empêcher.

Contre un VRAI PostgreSQL, parce que le sujet est le comportement de l'index : sa
capacité à collisionner entre deux formes ne se stube pas, et c'est elle qui autorise
à lever le refus posé sur la clé.
"""
from __future__ import annotations

import os

import psycopg
import pytest


@pytest.fixture()
def pg(pg_dsn, monkeypatch):
    """Une table `datastore_rows` minimale + le module pointé sur cette base."""
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute("CREATE TABLE datastore_rows ("
                  " ns_id INT, row_id TEXT, data JSONB,"
                  " created_at TIMESTAMPTZ DEFAULT now(),"
                  " updated_at TIMESTAMPTZ DEFAULT now())")
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _ins(c, ns, rid, data):
    c.execute("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s,%s,%s::jsonb)",
              (ns, rid, data))


def test_the_index_collides_across_both_forms(pg):
    """LE fait qui autorise à lever le refus posé sur la clé métier : une valeur
    enveloppée collisionne avec la même valeur nue."""
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "552081317"}')
    datastore_ensure_key_index(1, "siren")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _ins(pg, 1, "b", '{"siren": "552081317"}')
    with pytest.raises(psycopg.errors.UniqueViolation):
        _ins(pg, 1, "c", '{"siren": {"valeur": "552081317", "comment": "registre"}}')


def test_a_layered_key_collides_with_a_layered_key(pg):
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": {"valeur": "552081317"}}')
    datastore_ensure_key_index(1, "siren")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _ins(pg, 1, "b", '{"siren": {"valeur": "552081317", "comment": "x"}}')


def test_distinct_values_still_coexist(pg):
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "111111111"}')
    datastore_ensure_key_index(1, "siren")
    _ins(pg, 1, "b", '{"siren": {"valeur": "222222222"}}')
    n = pg.execute("SELECT count(*) FROM datastore_rows").fetchone()[0]
    assert n == 2


def test_the_index_is_partial_to_its_namespace(pg):
    """Un même SIREN dans DEUX tableaux n'est pas un doublon."""
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "552081317"}')
    datastore_ensure_key_index(1, "siren")
    _ins(pg, 2, "b", '{"siren": "552081317"}')


def test_rebuilding_is_idempotent(pg):
    """Rejouable : la migration de boot tourne à CHAQUE démarrage."""
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "552081317"}')
    for _ in range(3):
        datastore_ensure_key_index(1, "siren")
    idx = pg.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'datastore_rows'"
    ).fetchall()
    noms = sorted(r[0] for r in idx)
    assert noms == ["ds_bkey_1"], f"un index temporaire a survécu : {noms}"


def test_no_uniqueness_gap_during_the_rebuild(pg):
    """CRÉER AVANT DE DÉPOSER : à aucun instant la table n'est sans contrainte.

    Un DROP suivi d'un CREATE laisserait une fenêtre où un batch concurrent insère
    des doublons — que l'index neuf ne pourrait alors plus se créer par-dessus, ce
    qui transforme une fenêtre de quelques millisecondes en panne durable."""
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "552081317"}')
    datastore_ensure_key_index(1, "siren")
    datastore_ensure_key_index(1, "siren")      # rebuild sur une table déjà indexée
    with pytest.raises(psycopg.errors.UniqueViolation):
        _ins(pg, 1, "b", '{"siren": "552081317"}')


def test_an_existing_duplicate_refuses_the_index_rather_than_lying(pg):
    """Une table qui porte DÉJÀ un doublon ne peut pas recevoir l'index — et c'est
    la bonne issue : la migration de boot journalise et passe au suivant, plutôt que
    de faire croire à une unicité qu'elle n'impose pas."""
    from oto_mcp.db.datastore import datastore_ensure_key_index
    _ins(pg, 1, "a", '{"siren": "552081317"}')
    _ins(pg, 1, "b", '{"siren": {"valeur": "552081317"}}')
    with pytest.raises(psycopg.errors.Error):
        datastore_ensure_key_index(1, "siren")
