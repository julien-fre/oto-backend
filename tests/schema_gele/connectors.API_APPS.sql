CREATE TABLE IF NOT EXISTS api_apps (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
account TEXT NOT NULL CHECK (account <> ''),
connection JSONB NOT NULL DEFAULT '{}'::jsonb,
definition JSONB NOT NULL DEFAULT '{}'::jsonb,
created_by TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_by TEXT,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
archived_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS api_apps_un_nom_vivant
ON api_apps (org_id, account) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_apps_created_by
ON api_apps (created_by) WHERE created_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_api_apps_updated_by
ON api_apps (updated_by) WHERE updated_by IS NOT NULL;
