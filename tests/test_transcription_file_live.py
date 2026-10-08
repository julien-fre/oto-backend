"""La file de transcription contre un VRAI PostgreSQL (signal oto #1370).

Un agent qui dépose 20 fichiers relit le statut : il doit savoir combien de travaux
passent avant le sien, et ne jamais attendre un travail que plus aucun tour ne porte
(processus arrêté en plein appel). Ce qui se prouve ici, et qu'un banc à doublures ne
dirait pas : les deux requêtes de `db.transcription` sur la vraie table.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_transc_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
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


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(sql, params)


def _travail(pid, *, status="pending", age_s=0, fin_il_y_a_s=None):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        maj = age_s if fin_il_y_a_s is None else fin_il_y_a_s
        return conn.execute(
            "INSERT INTO transcription_jobs (project_id, sub, status, audio_key, "
            "  filename, api_key_enc, created_at, updated_at) "
            "VALUES (%s, 'u1', %s, 'k', 'a.mp3', 'x', "
            "  NOW() - make_interval(secs => %s), NOW() - make_interval(secs => %s)) "
            "RETURNING id", (pid, status, age_s, maj)).fetchone()["id"]


@pytest.fixture
def projet(live):
    from oto_mcp import db
    _exec("DELETE FROM transcription_jobs")
    return db.create_project("user", "u1", "transcriptions")


def test_la_place_en_file_compte_les_travaux_plus_anciens(projet):
    from oto_mcp import db
    _travail(projet, age_s=300)
    _travail(projet, age_s=200)
    moi = _travail(projet, age_s=100)
    _travail(projet, age_s=50)                          # après moi : ne compte pas
    _travail(projet, status="running", age_s=400)       # en cours : n'est plus devant
    assert db.transcription_queue(moi)["ahead"] == 2


def test_les_fins_recentes_mesurent_le_debit_plus_recente_d_abord(projet):
    from oto_mcp import db
    for il_y_a in (40, 100, 160):
        _travail(projet, status="done", age_s=600, fin_il_y_a_s=il_y_a)
    _travail(projet, status="failed", age_s=600, fin_il_y_a_s=220)
    _travail(projet, status="done", age_s=9000, fin_il_y_a_s=7200)   # hors de l'heure
    moi = _travail(projet)
    fins = db.transcription_queue(moi)["finishes"]
    assert len(fins) == 4
    assert fins == sorted(fins, reverse=True)


def test_un_travail_running_orphelin_est_clos_et_le_dit(projet):
    from oto_mcp import db
    orphelin = _travail(projet, status="running", age_s=900)
    vivant = _travail(projet, status="running", age_s=60)
    assert db.fail_orphaned_transcription_jobs() == 1
    o = db.get_transcription_job(orphelin)
    assert o["status"] == "failed" and "relance transcription_create" in o["error"]
    assert db.get_transcription_job(vivant)["status"] == "running"
    assert db.fail_orphaned_transcription_jobs() == 0
