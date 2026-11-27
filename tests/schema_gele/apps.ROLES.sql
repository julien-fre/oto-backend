ALTER TABLE resource_grants ADD COLUMN IF NOT EXISTS app_role TEXT;
ALTER TABLE resource_grants ADD COLUMN IF NOT EXISTS app_scope JSONB;
