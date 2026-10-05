CREATE TABLE IF NOT EXISTS user_datastores (
id BIGSERIAL PRIMARY KEY,
owner_type TEXT NOT NULL DEFAULT 'user',
owner_id TEXT,
namespace TEXT NOT NULL,
schema JSONB,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
context_org_id BIGINT REFERENCES orgs(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS datastore_rows (
ns_id BIGINT NOT NULL REFERENCES user_datastores(id) ON DELETE CASCADE,
row_id TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
data JSONB NOT NULL DEFAULT '{}'::jsonb,
claimed_by TEXT,
claimed_until TIMESTAMPTZ,
claimed_run TEXT,
claims INTEGER NOT NULL DEFAULT 0,
abandon_reason TEXT,
abandon_run TEXT,
claimed_at TIMESTAMPTZ,
rev BIGINT NOT NULL DEFAULT 0,
PRIMARY KEY (ns_id, row_id)
);
