CREATE TABLE IF NOT EXISTS copilot_attachments (
id TEXT PRIMARY KEY,
sub TEXT NOT NULL,
org_id BIGINT,
s3_key TEXT NOT NULL,
name TEXT NOT NULL,
media_type TEXT NOT NULL,
size BIGINT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_copilot_attachments_created
ON copilot_attachments (created_at);
