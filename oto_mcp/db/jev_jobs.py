"""Queue of `jev_rows` background jobs — see `db/schema/jev.py` for the table.

⚠️ Names are PREFIXED `jev_job` (never a bare `create_job`/`get_job`): `db/__init__.py`
flattens every module into the same `db.*` namespace, and `runner_jobs` already holds
`get_job`/`claim_next_job` with another shape.
"""
from __future__ import annotations

import json
from typing import Optional

from ._conn import _connect

#: States a job can still move from.
ACTIVE = ("pending", "running")
#: Errors kept on the job (the count goes on).
MAX_ERRORS = 20

_COLONNES = ("id, org_id, sub, project_id, run_id, adresse, model_column, params, status, "
             "cancel_requested, cursor, final_pass, counters, errors, error_count, cost, "
             "remaining, model, error, slices, stalled, leased_until, created_at, updated_at, "
             "finished_at")


class JevJobActive(Exception):
    """Another job is still active on the same table and `model_column`."""

    def __init__(self, job_id: Optional[int]):
        super().__init__(job_id)
        self.job_id = job_id


def create_jev_job(*, org_id: Optional[int], sub: str, project_id: Optional[int],
                   run_id: Optional[str], adresse: str, model_column: str,
                   params: dict, remaining: Optional[int]) -> int:
    """The `pending` job; raises `JevJobActive` when one already runs on these rows."""
    import psycopg
    try:
        with _connect() as conn:
            row = conn.execute(
                "INSERT INTO jev_jobs (org_id, sub, project_id, run_id, adresse, "
                "  model_column, params, remaining) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                (org_id, sub, project_id, run_id, adresse, model_column,
                 json.dumps(params), remaining),
            ).fetchone()
            return int(row["id"])
    except psycopg.errors.UniqueViolation:
        raise JevJobActive(active_jev_job(adresse, model_column)) from None


def active_jev_job(adresse: str, model_column: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM jev_jobs WHERE adresse = %s AND model_column = %s "
            "AND status IN ('pending', 'running')", (adresse, model_column),
        ).fetchone()
        return int(row["id"]) if row else None


def get_jev_job(job_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(f"SELECT {_COLONNES} FROM jev_jobs WHERE id = %s",
                           (int(job_id),)).fetchone()
        return dict(row) if row else None


def claim_next_jev_job(lease_s: int) -> Optional[dict]:
    """Take ATOMICALLY the active job waiting longest whose lease is free (`FOR UPDATE
    SKIP LOCKED`), lease it for `lease_s` and mark it `running`. One slice per claim:
    jobs take turns, none starves the others."""
    with _connect() as conn:
        row = conn.execute(
            f"""
            WITH pris AS (
                SELECT id FROM jev_jobs
                 WHERE status IN ('pending', 'running')
                   AND (leased_until IS NULL OR leased_until < NOW())
                 ORDER BY updated_at
                   FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE jev_jobs j
               SET status = 'running', leased_until = NOW() + make_interval(secs => %s),
                   updated_at = NOW()
              FROM pris WHERE j.id = pris.id
            RETURNING {', '.join('j.' + c.strip() for c in _COLONNES.split(','))}
            """, (int(lease_s),),
        ).fetchone()
        return dict(row) if row else None


def record_jev_job_slice(job_id: int, *, counters: dict, errors: list[dict],
                         error_count: int, cost: float, cursor: Optional[str],
                         final_pass: bool, remaining: Optional[int],
                         model: Optional[str], stalled: bool) -> dict:
    """Add one slice's counts to the job and move its resume point. Releases the lease.
    Returns `{status, cancel_requested, stalled}` as they are now."""
    with _connect() as conn:
        row = conn.execute(
            """
            UPDATE jev_jobs SET
                counters = (SELECT COALESCE(jsonb_object_agg(k, v), '{}'::jsonb) FROM (
                    SELECT k, SUM(v::numeric) AS v FROM (
                        SELECT key AS k, value AS v FROM jsonb_each_text(counters)
                        UNION ALL
                        SELECT key, value FROM jsonb_each_text(%s::jsonb)) s
                    GROUP BY k) t),
                errors = (SELECT COALESCE(jsonb_agg(e), '[]'::jsonb) FROM (
                    SELECT e FROM jsonb_array_elements(errors || %s::jsonb) e
                    LIMIT %s) t),
                error_count = error_count + %s,
                cost = cost + %s,
                cursor = %s, final_pass = %s, remaining = %s,
                model = COALESCE(model, %s),
                slices = slices + 1,
                stalled = CASE WHEN %s THEN stalled + 1 ELSE 0 END,
                leased_until = NULL, updated_at = NOW()
            WHERE id = %s
            RETURNING status, cancel_requested, stalled
            """,
            (json.dumps(counters), json.dumps(errors), MAX_ERRORS, int(error_count),
             float(cost), cursor, bool(final_pass), remaining, model, bool(stalled),
             int(job_id)),
        ).fetchone()
        return dict(row) if row else {}


def finish_jev_job(job_id: int, status: str, *, error: Optional[str] = None) -> None:
    """`done`, `failed` or `cancelled` — only from an active state."""
    with _connect() as conn:
        conn.execute(
            "UPDATE jev_jobs SET status = %s, error = COALESCE(%s, error), "
            "  leased_until = NULL, updated_at = NOW(), finished_at = NOW() "
            "WHERE id = %s AND status IN ('pending', 'running')",
            (status, error, int(job_id)),
        )


def cancel_jev_job(job_id: int) -> Optional[str]:
    """Ask a job to stop. A job with no slice in flight stops at once; one mid-slice
    stops when that slice ends. Returns the status as it is now."""
    with _connect() as conn:
        row = conn.execute(
            """
            UPDATE jev_jobs SET cancel_requested = TRUE,
                status = CASE WHEN leased_until IS NULL OR leased_until < NOW()
                              THEN 'cancelled' ELSE status END,
                finished_at = CASE WHEN leased_until IS NULL OR leased_until < NOW()
                                   THEN NOW() ELSE finished_at END,
                updated_at = NOW()
            WHERE id = %s AND status IN ('pending', 'running')
            RETURNING status
            """, (int(job_id),),
        ).fetchone()
        if row:
            return row["status"]
        cur = conn.execute("SELECT status FROM jev_jobs WHERE id = %s",
                           (int(job_id),)).fetchone()
        return cur["status"] if cur else None
