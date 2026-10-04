CREATE TABLE IF NOT EXISTS scheduled_emails (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
created_by TEXT,
to_email TEXT NOT NULL,
cc TEXT[],
subject TEXT NOT NULL,
body_html TEXT NOT NULL,
from_email TEXT,
from_name TEXT,
reply_to TEXT,
transport TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'pending',
scheduled_at TIMESTAMPTZ NOT NULL,
attempts INTEGER NOT NULL DEFAULT 0,
sent_at TIMESTAMPTZ,
error TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_emails(scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_sched_org ON scheduled_emails(org_id, status, created_at DESC);
