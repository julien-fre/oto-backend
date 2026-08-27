"""DDL du domaine « runs » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# runs, fil de messages, jobs et déclencheurs
RUNS = """
-- Runs / déroulés (ADR 0017, amende le « state-only » du barreau 1-2) : la
-- métadonnée SÉMANTIQUE d'un run (label, doctrine, outcome) est désormais PERSISTÉE
-- — la pile session-scopée de `doctrine_run.py` reste la source du run ACTIF (pour
-- stamper `tool_calls.run_id`), mais elle meurt avec la conversation. Cette table
-- donne la trace durable « l'user a déroulé telle doctrine, terminée tel outcome »
-- → anticipation du contexte injecté (#50 bloc C) + boucle d'usage dashboard. Le
-- DÉTAIL des appels d'un run reste corrélé via `tool_calls.run_id`. Table neuve →
-- indexes inline sûrs. `org_id` NULL hors org ; `outcome` NULL = run encore ouvert.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    sub TEXT,
    org_id BIGINT,
    project_id BIGINT,                          -- projet actif GELÉ au start (ADR 0032 §5/§6, B3) ; NULL hors projet
    label TEXT NOT NULL,
    doctrine TEXT,                              -- slug de la doctrine nommée ; NULL = run ad-hoc
    outcome TEXT,                               -- done|abandoned|failed|blocked ; NULL = ouvert
    note TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_runs_sub_org ON runs(sub, org_id, started_at DESC);
-- idx_runs_project est créé dans `_init` APRÈS l'ADD COLUMN project_id : sur une table
-- `runs` préexistante, CREATE TABLE IF NOT EXISTS est un no-op → la colonne n'existe
-- pas encore ici, un index la référençant dans _SCHEMA crashe au boot (vécu 2026-06-30,
-- même gotcha que idx_tool_calls_run/org ci-dessus).

-- Le FIL d'un run HÉBERGÉ (chantier runner R1 — ADR 0064 du blueprint) : l'état
-- d'exécution, PAS le journal. La reprise canonique inter-agents reste le journal ;
-- le fil sert à CONTINUER le même run (le worker le recharge, le dashboard le lit).
-- Il est EFFAÇABLE sans amputer le run — purge courte au boot (_init), et AUCUNE
-- fonction du produit ne doit l'exiger. Deux étages par tour : `content` = la
-- projection NEUTRE (ce que l'UI et l'API lisent, indépendante du fournisseur de
-- modèle) ; `provider_raw` = le tour provider exact (blocs de thinking inclus, à
-- réémettre verbatim pour une continuation fidèle) — NULL pour un message humain.
-- Hors recherche PAR CONSTRUCTION : jamais déclaré comme source (même règle que les
-- sous-arbres de run, 0058-D2). UNIQUE(run_id, seq) porte l'index de lecture.
CREATE TABLE IF NOT EXISTS run_messages (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq INT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content JSONB NOT NULL,
    provider_raw JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

-- La file d'EXÉCUTIONS du runner (chantier runner R2) — de la PLOMBERIE plateforme,
-- PAS une donnée du client : la file de LIGNES d'une campagne vit dans le datastore
-- de l'org (namespace client, `data_claim_next`) ; mélanger les deux ferait de la
-- plomberie une donnée visible du client. Même mécanique de bail (SKIP LOCKED),
-- table distincte — les deux baux coexistent sans se connaître.
-- `claimed_by` = le SUB du worker : l'audit d'un job (qui l'a pris, qui l'a fini)
-- en dépend. Le claim est SCOPÉ à l'org (V1 : un worker = un jeton d'org — le pool
-- multi-org attend l'arbitrage compte-de-service, ADR 0064 §5-1).
-- Un job à bout de tentatives est MARQUÉ `failed` (visible), jamais rejoué en
-- boucle : refuser-et-marquer, pas tourner.
CREATE TABLE IF NOT EXISTS runner_jobs (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('start', 'continue')),
    run_id TEXT REFERENCES runs(run_id) ON DELETE CASCADE,  -- NULL : start pas encore lié à son run
    payload JSONB,                       -- références SEULEMENT (procédure, projet, message) — jamais un secret
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'done', 'failed')),
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    claimed_by TEXT,
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    -- Le RÉSULTAT déclaré par le worker à la conclusion (usage_tokens, stopped,
    -- steps…) : c'est ce qui rend le coût d'un job LISIBLE par un ordonnanceur
    -- de flotte (garde budget) sans parser la note libre d'un run.
    result JSONB
);
CREATE INDEX IF NOT EXISTS idx_runner_jobs_claim
    ON runner_jobs(org_id, due_at) WHERE status = 'pending';

-- Les DÉCLENCHEURS du runner (chantier R3) : la CONFIG utilisateur qui FABRIQUE des
-- jobs — le tick les enfile à l'échéance (jamais d'exécution ici), le worker les
-- claime. `sub` = qui a posé le déclencheur (audit) ; le run tournera sous le
-- worker. `cron` s'évalue DANS `tz` (défaut Europe/Paris, ÉCRIT — « tous les
-- matins à 8h » doit dire quel 8h, sinon l'heure d'été décale toutes les veilles
-- d'une heure sans un mot). ⚠️ next_due se consomme par COMPARE-AND-SWAP : prod
-- et preprod partagent la même base, DEUX ticks tournent — un seul doit gagner
-- chaque échéance, l'autre voit le CAS échouer et passe.
CREATE TABLE IF NOT EXISTS runner_triggers (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL,
    sub TEXT NOT NULL,
    label TEXT,
    procedure TEXT NOT NULL,
    project_id BIGINT,
    tools JSONB NOT NULL,
    input TEXT,
    max_steps INT,
    cron TEXT NOT NULL,
    tz TEXT NOT NULL DEFAULT 'Europe/Paris',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_due TIMESTAMPTZ NOT NULL,
    last_enqueued_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_runner_triggers_due
    ON runner_triggers(next_due) WHERE enabled;
"""
