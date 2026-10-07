CREATE TABLE IF NOT EXISTS jev_jobs (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT,
sub TEXT NOT NULL,
project_id BIGINT,
run_id TEXT,
adresse TEXT NOT NULL,
model_column TEXT NOT NULL,
params JSONB NOT NULL,
status TEXT NOT NULL DEFAULT 'pending',
cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
cursor TEXT,
final_pass BOOLEAN NOT NULL DEFAULT FALSE,
counters JSONB NOT NULL DEFAULT '{}'::jsonb,
errors JSONB NOT NULL DEFAULT '[]'::jsonb,
error_count INTEGER NOT NULL DEFAULT 0,
cost DOUBLE PRECISION NOT NULL DEFAULT 0,
remaining INTEGER,
model TEXT,
error TEXT,
slices INTEGER NOT NULL DEFAULT 0,
stalled INTEGER NOT NULL DEFAULT 0,
leased_until TIMESTAMPTZ,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jev_jobs_actifs
ON jev_jobs(updated_at) WHERE status IN ('pending', 'running');
CREATE UNIQUE INDEX IF NOT EXISTS uq_jev_jobs_actif_par_colonne
ON jev_jobs(adresse, model_column) WHERE status IN ('pending', 'running');
