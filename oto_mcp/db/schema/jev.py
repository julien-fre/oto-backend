"""`jev_rows` in the background: the job queue (`jev_rows(background=true)`).

One row = one table to qualify, from the call that asked for it to the last row judged.
The worker (`jev_jobs_worker.py`) runs it slice by slice under the CALLER's identity, so
the key, the write right and the spending cap are re-checked on every slice; the row
carries what a slice needs to resume (`cursor`, `final_pass`) and what it has done so
far (`counters`, `cost`), written after every slice.
"""
from __future__ import annotations

JEV_JOBS = """
-- No key here: the shared Jev key is resolved again on every slice, under `sub` and
-- `org_id`, so a revoked grant or an exhausted quota stops the job at its next slice.
--
-- `params` = the call's arguments, already checked against the table at creation
-- (questions, state_fields, output, model_column, filter, filters, overwrite, model,
-- parallel). `adresse` = the table as resolved then; the write right on it is checked
-- again on every slice.
CREATE TABLE IF NOT EXISTS jev_jobs (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT,
    sub TEXT NOT NULL,
    project_id BIGINT,
    run_id TEXT,
    adresse TEXT NOT NULL,
    model_column TEXT NOT NULL,
    params JSONB NOT NULL,
    -- pending | running | done | failed | cancelled.
    status TEXT NOT NULL DEFAULT 'pending',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    -- Resume point: `jev_rows`' own cursor, then one pass without it (`final_pass`).
    cursor TEXT,
    final_pass BOOLEAN NOT NULL DEFAULT FALSE,
    counters JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- First errors only (`{_id, code}`), never a value read from the table.
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_count INTEGER NOT NULL DEFAULT 0,
    cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    remaining INTEGER,
    model TEXT,
    error TEXT,
    slices INTEGER NOT NULL DEFAULT 0,
    -- Consecutive slices that moved nothing (every send failed upstream): the job
    -- stops after a few rather than spin.
    stalled INTEGER NOT NULL DEFAULT 0,
    -- Lease of the worker running a slice: another process takes the job over only
    -- once it has expired (blue/green overlap, a process killed mid-slice).
    leased_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
-- Partial: only the population the worker looks at on every tick.
CREATE INDEX IF NOT EXISTS idx_jev_jobs_actifs
    ON jev_jobs(updated_at) WHERE status IN ('pending', 'running');
-- One active job per (table, model_column): two would judge and bill the same rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_jev_jobs_actif_par_colonne
    ON jev_jobs(adresse, model_column) WHERE status IN ('pending', 'running');
"""
