CREATE TABLE IF NOT EXISTS access_requests (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
requester_sub TEXT NOT NULL,
kind TEXT NOT NULL,
cible JSONB NOT NULL,
cle_cible TEXT NOT NULL,
demande JSONB NOT NULL DEFAULT '{}'::jsonb,
note TEXT,
status TEXT NOT NULL DEFAULT 'pending'
CHECK (status IN ('pending', 'approved', 'declined', 'cancelled', 'expired')),
decided_by TEXT,
decided_at TIMESTAMPTZ,
decision JSONB,
decision_note TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
reminded_at TIMESTAMPTZ,
expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '14 days'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_access_requests_en_attente
ON access_requests (requester_sub, kind, cle_cible) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_access_requests_org_en_attente
ON access_requests (org_id, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_access_requests_demandeur
ON access_requests (requester_sub, id);
CREATE INDEX IF NOT EXISTS idx_access_requests_decideur
ON access_requests (decided_by);
