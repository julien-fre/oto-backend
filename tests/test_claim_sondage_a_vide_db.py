"""Le sondage à VIDE ne coûte qu'une lecture (31/10/2026) : le ménage de `claim_next_job`
passe toutes les `_MENAGE_S` secondes quand rien n'est en vue, et à CHAQUE sondage dès
qu'un travail l'est. En SQL, donc sur vraie base.

⚠️ Ce que ce banc tient : ralentir le ménage ne doit jamais servir un travail périmé, ni
cacher un travail que la prise aurait servi.
"""
from __future__ import annotations

import os
import uuid

import pytest

ORG = 941_001
WORKER = "worker:sondage-a-vide"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_sondage_vide_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture(autouse=True)
def _file_vide(live, monkeypatch):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    monkeypatch.setenv("OTO_RUNNER_MENAGE_S", "3600")
    db.runner_jobs._menage_passe.clear()
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs WHERE org_id = %s", (ORG,))
    yield
    db.runner_jobs._menage_passe.clear()


def _execute(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(sql, params)


def _epave():
    """Un travail réservé, bail expiré, tentatives épuisées : ce que le ménage constate."""
    from oto_mcp import db
    job = db.enqueue_job(ORG, "start", payload={"procedure": "p"})
    _execute("UPDATE runner_jobs SET status = 'claimed', attempts = max_attempts, "
             "lease_until = NOW() - interval '1 minute' WHERE id = %s", (job["id"],))
    return job["id"]


def _statut(job_id):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT status FROM runner_jobs WHERE id = %s",
                            (job_id,)).fetchone()["status"]


def test_a_vide_le_menage_ne_repasse_pas_avant_son_intervalle(live):
    from oto_mcp import db
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None   # il passe
    epave = _epave()
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
    assert _statut(epave) == "claimed"
    db.runner_jobs._menage_passe.clear()                              # l'intervalle échu
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
    assert _statut(epave) == "failed"


def test_le_premier_sondage_a_vide_fait_le_menage(live):
    from oto_mcp import db
    epave = _epave()
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
    assert _statut(epave) == "failed"


def test_un_travail_en_vue_passe_le_menage_a_chaque_sondage(live):
    """Un travail perime ne part jamais, meme si le menage vient de passer."""
    from oto_mcp import db
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
    perime = db.enqueue_job(ORG, "start", payload={"procedure": "p", "_perime_apres_s": 1})
    _execute("UPDATE runner_jobs SET created_at = NOW() - interval '1 hour', "
             "payload = payload || '{\"_perime_apres_s\": 1}'::jsonb WHERE id = %s",
             (perime["id"],))
    frais = db.enqueue_job(ORG, "start", payload={"procedure": "p"})
    pris = db.claim_next_job(ORG, WORKER, lease_seconds=60)
    assert pris["id"] == frais["id"]
    assert _statut(perime["id"]) == "expired"


@pytest.mark.parametrize("preparer", ["pending", "bail_expire"])
def test_la_lecture_ne_cache_pas_ce_que_la_prise_sert(live, preparer):
    from oto_mcp import db
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
    job = db.enqueue_job(ORG, "start", payload={"procedure": "p"})
    if preparer == "bail_expire":
        _execute("UPDATE runner_jobs SET status = 'claimed', attempts = 1, "
                 "lease_until = NOW() - interval '1 minute' WHERE id = %s", (job["id"],))
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60)["id"] == job["id"]


def test_un_travail_a_venir_ne_compte_pas(live):
    from oto_mcp import db
    job = db.enqueue_job(ORG, "start", payload={"procedure": "p"})
    _execute("UPDATE runner_jobs SET due_at = NOW() + interval '1 hour' WHERE id = %s",
             (job["id"],))
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None


def test_une_campagne_vit_selon_son_statut(live):
    from oto_mcp import db
    f = db.create_fleet(ORG, "s", label="l", procedure="p", tools=["t"])
    assert db.une_campagne_vit(ORG) is False                          # brouillon
    for statut, vit in (("armed", True), ("running", True), ("stopping", True),
                        ("stopped", False), ("done", False)):
        _execute("UPDATE runner_fleets SET status = %s WHERE id = %s", (statut, f["id"]))
        assert db.une_campagne_vit(ORG) is vit, statut
    _execute("DELETE FROM runner_fleets WHERE id = %s", (f["id"],))
