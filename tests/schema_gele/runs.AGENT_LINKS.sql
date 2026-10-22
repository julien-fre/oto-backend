CREATE TABLE IF NOT EXISTS runner_trigger_links (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT NOT NULL,
from_trigger_id BIGINT NOT NULL REFERENCES runner_triggers(id) ON DELETE CASCADE,
to_trigger_id BIGINT NOT NULL REFERENCES runner_triggers(id) ON DELETE CASCADE,
kind TEXT NOT NULL CHECK (kind IN ('starts', 'requires')),
condition TEXT,
created_by TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
CHECK (from_trigger_id <> to_trigger_id),
UNIQUE (from_trigger_id, to_trigger_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_runner_trigger_links_org
ON runner_trigger_links(org_id);
CREATE INDEX IF NOT EXISTS idx_runner_trigger_links_to
ON runner_trigger_links(to_trigger_id, kind);
