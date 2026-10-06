CREATE TABLE IF NOT EXISTS tenants (
id BIGSERIAL PRIMARY KEY,
slug TEXT NOT NULL UNIQUE,
name TEXT NOT NULL,
issuer TEXT UNIQUE,
jwks_uri TEXT,
hosts JSONB NOT NULL DEFAULT '[]'::jsonb,
tool_prefix TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
disabled_at TIMESTAMPTZ,
disabled_by TEXT,
disabled_reason TEXT
);
CREATE TABLE IF NOT EXISTS tenant_admins (
slug TEXT NOT NULL REFERENCES tenants(slug) ON DELETE CASCADE,
sub TEXT NOT NULL,
granted_by TEXT,
granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (slug, sub)
);
