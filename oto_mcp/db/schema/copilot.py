"""DDL de l'agent intégré à l'application (« copilot », `docs/copilot.md`).

Deux tables, et seulement l'état d'une conversation en cours :

- `copilot_threads` : une conversation, LIÉE au compte qui l'a ouverte (`sub`) et à
  l'org consultée (`org_id`). Ses messages (forme de l'API du modèle, blocs de
  réflexion compris, rejoués tels quels), la file des appels d'outils du tour en cours,
  l'appel qui attend une réponse (une carte d'approbation, une action de page), les
  sortes de changement que la personne a dit de ne plus demander. `busy_*` = le verrou
  d'une étape : une seule à la fois par conversation, sur toutes les instances (bleu,
  vert, préprod — une seule base).
- `copilot_undos` : ce qu'un changement fait par l'agent sait défaire, une fois, avant
  son échéance.

Ce n'est PAS le journal : chaque appel d'outil est déjà dans `tool_calls`, sous le run
de la conversation. Ces tables se purgent sans rien amputer.
"""
from __future__ import annotations

COPILOT = """
-- Une conversation de l'agent intégré. `id` = `thr_<hex>`. Lue et reprise par son seul
-- `sub` : un autre compte reçoit « introuvable ».
CREATE TABLE IF NOT EXISTS copilot_threads (
    id TEXT PRIMARY KEY,
    sub TEXT NOT NULL,
    org_id BIGINT,
    project_id BIGINT,
    -- Le run ouvert pour la conversation ; NULL = clos (inactivité), rouvert au besoin.
    run_id TEXT,
    system TEXT NOT NULL DEFAULT '',
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    queue JSONB NOT NULL DEFAULT '[]'::jsonb,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,
    pending JSONB,
    remembered JSONB NOT NULL DEFAULT '[]'::jsonb,
    client_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- `ask` : chaque écriture attend la carte ; `act` : les écritures simples passent,
    -- envois, suppressions et lancements demandent toujours.
    mode TEXT NOT NULL DEFAULT 'ask',
    -- Appels au modèle déjà faits : le rang du suivant, clé de sa réservation.
    model_calls INTEGER NOT NULL DEFAULT 0,
    busy_token TEXT,
    busy_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_copilot_threads_sub ON copilot_threads (sub, last_seen);

-- Une annulation offerte. `kind` dit comment défaire, `payload` de quoi le faire.
CREATE TABLE IF NOT EXISTS copilot_undos (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES copilot_threads(id) ON DELETE CASCADE,
    sub TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);
"""
