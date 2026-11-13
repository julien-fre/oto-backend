CREATE TABLE IF NOT EXISTS org_favorites (
org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
kind TEXT NOT NULL CHECK (kind IN ('doc', 'table')),
ref_id BIGINT NOT NULL,
added_by TEXT,
added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (org_id, kind, ref_id)
);
