CREATE TABLE IF NOT EXISTS unipile_sending_limits (
account_id TEXT NOT NULL,
lim TEXT NOT NULL,
max_count INTEGER NOT NULL CHECK (max_count >= 0),
sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (account_id, lim)
);
CREATE TABLE IF NOT EXISTS unipile_sending_log (
id BIGSERIAL PRIMARY KEY,
account_id TEXT NOT NULL,
action TEXT NOT NULL,
sub TEXT REFERENCES users(sub) ON DELETE CASCADE,
at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_unipile_sending_log_acct
ON unipile_sending_log(account_id, action, at);
CREATE INDEX IF NOT EXISTS idx_unipile_sending_limits_sub ON unipile_sending_limits(sub);
CREATE INDEX IF NOT EXISTS idx_unipile_sending_log_sub ON unipile_sending_log(sub);
