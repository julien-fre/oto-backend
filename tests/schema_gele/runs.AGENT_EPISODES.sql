CREATE TABLE IF NOT EXISTS agent_episodes (
id BIGSERIAL PRIMARY KEY,
org_id BIGINT NOT NULL,
trigger_id BIGINT NOT NULL,
classe TEXT NOT NULL,
cote TEXT NOT NULL CHECK (cote IN ('plateforme', 'proprietaire', 'inconnu')),
statut TEXT NOT NULL DEFAULT 'ouvert'
CHECK (statut IN ('ouvert', 'en_soin', 'escalade', 'clos')),
motif TEXT,
dernier_job_id BIGINT,
travaux_morts INTEGER NOT NULL DEFAULT 0,
reparations JSONB NOT NULL DEFAULT '[]'::jsonb,
diagnostic TEXT,
ouvert_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
vu_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
clos_at TIMESTAMPTZ,
resolution TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_episodes_vivant
ON agent_episodes(trigger_id) WHERE statut <> 'clos';
CREATE INDEX IF NOT EXISTS idx_agent_episodes_statut
ON agent_episodes(statut, vu_at DESC);
