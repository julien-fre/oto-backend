CREATE TABLE IF NOT EXISTS usage (
sub TEXT NOT NULL,
tool TEXT NOT NULL,
day DATE NOT NULL,
count INTEGER NOT NULL DEFAULT 0,
PRIMARY KEY (sub, tool, day)
);
CREATE TABLE IF NOT EXISTS tool_calls (
id BIGSERIAL PRIMARY KEY,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
server TEXT NOT NULL DEFAULT 'oto',
kind TEXT NOT NULL DEFAULT 'mcp',
sub TEXT,
email TEXT,
tool TEXT NOT NULL,
args JSONB,
ok BOOLEAN NOT NULL DEFAULT TRUE,
error TEXT,
duration_ms INTEGER,
result_size INTEGER,
result_shape TEXT CONSTRAINT tool_calls_result_shape_ferme CHECK (result_shape ~ '^(empty|non_empty|refused[(][a-z][a-z_]{0,39}[)])$'),
session_id TEXT,
run_id TEXT,
org_id BIGINT,
client_id TEXT,
sentry_event_id TEXT,
request_id TEXT,
call_uid TEXT,
effective_sub TEXT,
error_kind TEXT,
token_id BIGINT,
token_kind TEXT,
quantity INTEGER,
key_mode TEXT,
provider_credits INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_created_at ON tool_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_calls_sub ON tool_calls(sub);
CREATE INDEX IF NOT EXISTS idx_tool_call_log_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ns ON tool_calls ((args->>'ns_id'), created_at DESC)
WHERE args->>'ns_id' IS NOT NULL;
CREATE TABLE IF NOT EXISTS usage_signals (
id BIGSERIAL PRIMARY KEY,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
sub TEXT,
org_id BIGINT,
signal TEXT NOT NULL,
kind TEXT NOT NULL,
target TEXT,
body TEXT,
session_id TEXT,
source TEXT NOT NULL DEFAULT 'agent',
status TEXT NOT NULL DEFAULT 'open',
resolved_at TIMESTAMPTZ,
resolved_by TEXT,
resolution TEXT,
notified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_usage_signals_signal ON usage_signals(signal, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_signals_target ON usage_signals(signal, target, created_at DESC);
