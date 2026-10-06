CREATE TABLE IF NOT EXISTS usage_signal_occurrences (
id BIGSERIAL PRIMARY KEY,
signal_id BIGINT NOT NULL REFERENCES usage_signals(id),
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
sub TEXT,
kind TEXT NOT NULL,
body TEXT,
session_id TEXT,
source TEXT NOT NULL DEFAULT 'agent'
);
CREATE INDEX IF NOT EXISTS idx_usage_signal_occurrences_signal
ON usage_signal_occurrences(signal_id, created_at DESC);
