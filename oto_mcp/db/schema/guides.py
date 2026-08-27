"""DDL du domaine « guides » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# instructions plateforme et guides (ADR 0042)
GUIDES = """
-- Instructions injectées AU NIVEAU PLATEFORME (#50, bloc A « secret sauce » +
-- bloc B « onboarding »). Singleton par `key` ('secret_sauce' | 'onboarding').
-- Éditable seulement par l'admin plateforme (inviolable par l'org — frontière
-- plateforme/org nette). Seedé au boot depuis les constantes de `instructions.py`
-- (INSERT ON CONFLICT DO NOTHING) → le code reste le défaut/fallback, la DB porte
-- l'override éditable. En CLAIR (prose, pas un credential).
CREATE TABLE IF NOT EXISTS platform_instructions (
    key TEXT PRIMARY KEY,                       -- 'secret_sauce' (bloc A) | 'onboarding' (bloc B)
    body_md TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

-- Guides (ADR 0042) — PROSE d'instruction, UNE table pour deux LIVRAISONS :
--   * delivery='on-demand' : how-to chargé à la demande via `oto_guide`
--     (scope org|user en DB ; platform on-demand = fichiers `guides/*.md`, PR) ;
--   * delivery='init' : readme injecté au handshake (bloc A/C) — le MÊME primitif,
--     migré des ex-tables (secret_sauce, *_instructions[claude_md], user_agent_readme).
-- Distincte des PROCÉDURES (`org_instructions`, slots/versioning). CLAIR (pas un credential).
--
-- ⚠️ TABLE EN LECTURE SEULE depuis le lot M1 (blueprint ADR 0063-D4) : ses lignes
-- vivent désormais dans `nodes` (voir juste dessous), plus rien ici ne s'écrit par
-- la façade `db/guides.py`. Elle reste en place — la PROD tourne encore l'ancien
-- code sur CETTE MÊME base, et la conversion la recopie à chaque boot pour
-- rattraper ce qu'elle y écrit. Les deux lecteurs qui vivaient hors façade — la
-- recherche (`db/search.py`) et l'outbox d'embeddings (`db/aux_embed.py`) — sont
-- passés sur `nodes` (#282) : un guide écrit depuis M1 était sorti de `oto_search`
-- sans que rien ne le dise.
-- ⚠️ **Rien ici ne se DROPPE tant que la prod n'a pas été taguée** : ni la table,
-- ni ses colonnes, ni ses index de recherche `idx_guides_fts`/`idx_guides_trgm`
-- (posés par `search.index_ddl`), ni les lignes `aux_embeddings(kind='guide')`.
-- L'ancien code s'en sert en production : les retirer aujourd'hui y casserait la
-- recherche instantanément. C'est le lot d'après (docs/live-migrations.md).
CREATE TABLE IF NOT EXISTS guides (
    id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL,                         -- 'platform' | 'org' | 'group' | 'user'
    owner_id TEXT NOT NULL,                      -- 'platform' | org.id::text | group.id::text | sub
    slug TEXT NOT NULL,                          -- 'readme'/'secret_sauce' (init) | how-to slug
    delivery TEXT NOT NULL DEFAULT 'on-demand',  -- 'init' | 'on-demand'
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope, owner_id, slug)
);
"""
