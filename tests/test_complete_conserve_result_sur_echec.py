"""`complete(ok=false)` écrit le `result` déclaré, comme `ok=true`.

Mesuré le 13/09/2026 sur un banc jetable : la branche d'échec de `complete_job` ne
posait pas `result` — validé ≤ 4 Ko par la capacité, puis jeté. `fleet_state` somme
`result->>'usage_tokens'` : 2 134 jetons déclarés par une tentative ratée, 0 dans la
flotte. Le coût d'un échec sortait de tout total, et la garde budget avec lui.

⚠️ Limite, pas promesse : `result` est UNE colonne. La conclusion suivante l'écrase,
les tentatives ne s'additionnent pas — le cas ② le fige pour qu'on ne le lise pas
comme un cumul.

Contre un PostgreSQL jetable (`OTO_TEST_PG_DSN`), sur le vrai `complete_job`. Chaque
conclusion porte l'`attempt_id` que le claim a rendu (bascule dure : `complete` l'exige).
"""
from __future__ import annotations

import uuid

import pytest

WORKER = "worker-echec-result"
SUB = "sub-echec-result"


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_echec_result_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
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


# Une org PAR CAS : la réservation prend le plus ancien travail de l'org, et un cas
# qui laisse un `pending` derrière lui ne doit pas servir le travail du suivant.

def _flotte(org: int) -> int:
    from oto_mcp import db
    return int(db.create_fleet(org, SUB, label="passage", procedure="p",
                               tools=["data_write"])["id"])


def _job_claime(org: int, *, max_attempts: int = 3,
                fleet_id: int | None = None) -> tuple[int, str]:
    """Rend (travail, tentative) : la tentative est ce qui conclut (attempt_id exigé)."""
    from oto_mcp import db
    j = db.enqueue_job(org, "start", payload={"procedure": "p"},
                       max_attempts=max_attempts, fleet_id=fleet_id)
    job = db.claim_next_job(org, WORKER, lease_seconds=60)
    assert job and job["id"] == j["id"], "la file portait ce travail : le claim le rend"
    return int(j["id"]), job["attempt_id"]


def _ligne(job_id: int) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return dict(conn.execute(
            "SELECT status, result, last_error, claimed_by, lease_until, finished_at, "
            "       attempts, due_at > NOW() AS en_backoff "
            "  FROM runner_jobs WHERE id = %s", (job_id,)).fetchone())


def _sql(requete: str, *args) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(requete, args)


def _jetons_de_flotte(fleet_id: int, org: int) -> int:
    from oto_mcp import db
    return db.fleet_state(fleet_id, org)["state"]["usage_tokens"]


def _refile_inchangee(l: dict) -> None:
    """Ce que la re-file faisait déjà, et qui ne doit pas bouger."""
    assert l["status"] == "pending", l
    assert l["claimed_by"] is None and l["lease_until"] is None, l
    assert l["finished_at"] is None and l["en_backoff"] is True, l


# ── ① un échec relançable écrit son result, et la flotte le compte ──────────────

def test_un_echec_relancable_ecrit_son_result_et_la_flotte_le_compte(live):
    from oto_mcp import db as d
    org = 9131
    fid = _flotte(org)
    job_id, tentative = _job_claime(org, max_attempts=2, fleet_id=fid)

    declare = {"usage_tokens": 1234, "usage_cache_read": 99,
               "stopped": "error", "steps": 3}
    out = d.complete_job(job_id, WORKER, False, error="l'agent a planté",
                         result=declare, attempt_id=tentative)
    assert out == {"status": "pending", "run_id": None}, out

    l = _ligne(job_id)
    assert l["result"] == declare, (
        f"le résultat déclaré d'un échec est écrit, pas jeté — {l['result']!r}")
    assert _jetons_de_flotte(fid, org) == 1234, (
        "le coût d'une tentative ratée entre dans l'état de la flotte")
    assert l["last_error"] == "l'agent a planté"
    _refile_inchangee(l)


# ── ② la tentative finale ratée écrit le sien — et écrase le précédent ──────────

def test_la_tentative_finale_ratee_ecrit_son_result(live):
    from oto_mcp import db as d
    org = 9132
    fid = _flotte(org)
    job_id, tentative = _job_claime(org, max_attempts=2, fleet_id=fid)

    d.complete_job(job_id, WORKER, False, error="t1", result={"usage_tokens": 1234},
                   attempt_id=tentative)
    _sql("UPDATE runner_jobs SET due_at = NOW() WHERE id = %s", job_id)  # backoff écoulé
    reprise = d.claim_next_job(org, WORKER, lease_seconds=60)
    assert reprise and reprise["id"] == job_id and reprise["attempts"] == 2, reprise

    finale = {"usage_tokens": 567, "usage_cache_read": 8, "stopped": "error"}
    out = d.complete_job(job_id, WORKER, False, error="t2", result=finale,
                         attempt_id=reprise["attempt_id"])
    assert out == {"status": "failed", "run_id": None}, out

    l = _ligne(job_id)
    assert l["result"] == finale, (
        f"le résultat de la tentative finale est écrit — {l['result']!r}")
    assert l["status"] == "failed" and l["finished_at"] is not None, l
    assert l["last_error"] == "t2" and l["attempts"] == 2, l
    assert _jetons_de_flotte(fid, org) == 567, (
        "LIMITE, pas cumul : la conclusion suivante écrase `result` — la flotte "
        "compte 567, pas 1234 + 567")


# ── ③ sans result : rien n'est écrasé par NULL ──────────────────────────────────

@pytest.mark.parametrize("org, avant", [(9133, None), (9134, {"usage_tokens": 42})])
def test_un_echec_sans_result_ne_touche_pas_au_result(live, org, avant):
    """Aligné sur `ok=true` (`COALESCE`). Le `result` préexistant est posé HORS de
    `complete_job` : ce cas vaut sur l'ancien code comme sur le nouveau."""
    from oto_mcp import db as d
    job_id, tentative = _job_claime(org)
    if avant is not None:
        _sql("UPDATE runner_jobs SET result = %s::jsonb WHERE id = %s",
             '{"usage_tokens": 42}', job_id)

    out = d.complete_job(job_id, WORKER, False, attempt_id=tentative)
    assert out == {"status": "pending", "run_id": None}, out

    l = _ligne(job_id)
    assert l["result"] == avant, l["result"]
    assert l["last_error"] == "échec non détaillé", l
    _refile_inchangee(l)


# ── ④ témoin : le succès écrit son result, comme avant ──────────────────────────

def test_temoin_le_succes_ecrit_son_result(live):
    from oto_mcp import db as d
    org = 9135
    fid = _flotte(org)
    job_id, tentative = _job_claime(org, fleet_id=fid)

    declare = {"usage_tokens": 31500, "usage_cache_read": 120, "stopped": "end_turn"}
    out = d.complete_job(job_id, WORKER, True, result=declare, attempt_id=tentative)
    assert out == {"status": "done", "run_id": None}, out

    l = _ligne(job_id)
    assert l["result"] == declare and l["last_error"] is None, l
    assert l["status"] == "done" and l["finished_at"] is not None, l
    assert _jetons_de_flotte(fid, org) == 31500
