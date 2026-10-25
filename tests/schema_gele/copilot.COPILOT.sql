CREATE TABLE IF NOT EXISTS copilot_threads (
id TEXT PRIMARY KEY,
sub TEXT NOT NULL,
org_id BIGINT,
project_id BIGINT,
run_id TEXT,
system TEXT NOT NULL DEFAULT '',
messages JSONB NOT NULL DEFAULT '[]'::jsonb,
queue JSONB NOT NULL DEFAULT '[]'::jsonb,
results JSONB NOT NULL DEFAULT '[]'::jsonb,
pending JSONB,
remembered JSONB NOT NULL DEFAULT '[]'::jsonb,
client_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
mode TEXT NOT NULL DEFAULT 'ask',
model_calls INTEGER NOT NULL DEFAULT 0,
busy_token TEXT,
busy_until TIMESTAMPTZ,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_copilot_threads_sub ON copilot_threads (sub, last_seen);
CREATE TABLE IF NOT EXISTS copilot_undos (
id TEXT PRIMARY KEY,
thread_id TEXT NOT NULL REFERENCES copilot_threads(id) ON DELETE CASCADE,
sub TEXT NOT NULL,
label TEXT NOT NULL DEFAULT '',
kind TEXT NOT NULL,
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
expires_at TIMESTAMPTZ NOT NULL,
used_at TIMESTAMPTZ
);
