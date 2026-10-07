CREATE TABLE IF NOT EXISTS recipe_leases (
lock_key TEXT PRIMARY KEY,
holder TEXT NOT NULL,
until TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS recipe_pending_jobs (
recipe_id BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
params_key TEXT NOT NULL,
job JSONB NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (recipe_id, params_key)
);
CREATE TABLE IF NOT EXISTS recipe_schedules (
id BIGSERIAL PRIMARY KEY,
recipe_id BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
sub TEXT NOT NULL,
org_id BIGINT,
params JSONB NOT NULL DEFAULT '{}'::jsonb,
datastore TEXT NOT NULL,
every_minutes INTEGER NOT NULL CHECK (every_minutes >= 15),
next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
enabled BOOLEAN NOT NULL DEFAULT TRUE,
failures INTEGER NOT NULL DEFAULT 0,
last_run_at TIMESTAMPTZ,
last_receipt JSONB,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS recipe_schedules_due
ON recipe_schedules (next_run_at) WHERE enabled;
