CREATE TABLE IF NOT EXISTS slack_agent_posts (
channel TEXT NOT NULL,
ts TEXT NOT NULL,
org_id BIGINT NOT NULL,
trigger_id BIGINT NOT NULL REFERENCES runner_triggers(id) ON DELETE CASCADE,
job_id BIGINT,
author TEXT NOT NULL DEFAULT 'app' CHECK (author IN ('app', 'me')),
thread_ts TEXT,
text TEXT NOT NULL DEFAULT '',
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (channel, ts)
);
CREATE INDEX IF NOT EXISTS idx_slack_agent_posts_created
ON slack_agent_posts(created_at);
