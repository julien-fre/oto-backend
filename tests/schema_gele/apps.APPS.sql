CREATE TABLE IF NOT EXISTS apps (
id BIGSERIAL PRIMARY KEY,
project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
title TEXT NOT NULL,
manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
html TEXT NOT NULL DEFAULT '',
thumbnail JSONB,
created_by TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_by TEXT,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
archived_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS apps_project_idx ON apps (project_id) WHERE archived_at IS NULL;
CREATE TABLE IF NOT EXISTS app_revisions (
id BIGSERIAL PRIMARY KEY,
app_id BIGINT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
title TEXT NOT NULL,
manifest JSONB NOT NULL,
html TEXT NOT NULL,
thumbnail JSONB,
edited_by TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS app_revisions_app_idx ON app_revisions (app_id, id DESC);
