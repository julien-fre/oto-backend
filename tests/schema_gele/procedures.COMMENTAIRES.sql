CREATE TABLE IF NOT EXISTS procedure_comments (
id BIGSERIAL PRIMARY KEY,
instruction_id BIGINT NOT NULL,
version INTEGER,
auteur_sub TEXT,
body TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_procedure_comments_procedure
ON procedure_comments (instruction_id, id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_procedure_comments_auteur
ON procedure_comments (auteur_sub);
