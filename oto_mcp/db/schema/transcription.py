"""Transcription (ADR 0074) : la file des travaux asynchrones.

Une ligne = un appel demandé, du dépôt jusqu'au résultat. Contrairement à
`project_file_texts` (l'ABSENCE de ligne est la file), ici la ligne NAÎT à la
demande : l'appel Mistral peut durer jusqu'à 300 s, et il faut distinguer
« pas encore réclamé » (`pending`) de « en cours d'appel au fournisseur »
(`running`) — sans ça, deux tours du worker pourraient réclamer deux fois le
même travail avant que le premier n'ait répondu.
"""
from __future__ import annotations

TRANSCRIPTION_JOBS = """
-- La clé du connecteur est CHIFFRÉE (`api_key_enc`, même enveloppe que le coffre,
-- `oto_mcp/crypto.py`) : elle est résolue à la demande, dans le contexte de
-- l'appel MCP (seul endroit où la cascade byo_user résout), puis portée jusqu'au
-- worker qui, lui, tourne hors de tout contexte d'appel. Aucune colonne
-- plaintext (même règle que le coffre).
--
-- `audio_key` pointe l'OBJET temporaire dans le média store (pas les octets en
-- base) : l'audio est déjà téléchargé au dépôt du travail (encore dans le
-- contexte d'appel, pour la garde d'accès au fichier source), le worker n'a
-- plus qu'à le relire. Purgé (`delete_by_key`) après le tour, succès ou échec —
-- une seule tentative, jamais de copie qui traîne.
CREATE TABLE IF NOT EXISTS transcription_jobs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sub TEXT NOT NULL,
    -- pending | running | done | failed. Pas de reprise automatique (#674) :
    -- un appel PAYANT qui échoue ne se retente pas tout seul, l'agent redemande.
    status TEXT NOT NULL DEFAULT 'pending',
    audio_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT,
    language TEXT,
    vocabulary TEXT,
    api_key_enc TEXT NOT NULL,
    page_id BIGINT,
    result JSONB,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Partiel : ne porte que la population que le worker regarde à chaque tour.
CREATE INDEX IF NOT EXISTS idx_transcription_jobs_pending
    ON transcription_jobs(created_at) WHERE status = 'pending';
"""
