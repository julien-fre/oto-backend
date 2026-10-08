CREATE TABLE IF NOT EXISTS user_model_subscription_orgs (
sub TEXT NOT NULL,
famille TEXT NOT NULL,
org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (sub, famille, org_id)
);
