"""DDL du domaine « connectors » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# RBAC connecteur interne à l'org
ACL = """
-- RBAC connecteur — table UNIQUE `connector_acl` (chantier ACL, cadrage 10/07 :
-- fusion d'org_connector_access + group_connector_access ; le grain est une COLONNE
-- de scope, pas une table par grain). Sémantique INCHANGÉE par scope :
-- · scope 'org' (ADR 0025) : l'org_admin réserve un connecteur à un sous-ensemble de
--   son org. ≥1 ligne pour (scope, connector) ⟹ RESTREINT (deny-by-default) ; absence
--   ⟹ ouvert à tous les membres. principal = un groupe (department) ou un user. DUR :
--   enforced en visibilité + au call-time (access.require_connector_access) ;
--   l'escalade org_admin transcende (0044 §G). Ouvert par défaut = zéro disruption.
-- · scope 'group' (ADR 0012 B2, restrict-only) : narrowing pur de l'ACL d'org — le
--   principal est toujours un MEMBRE ('user', sub) ; l'équipe restreint davantage,
--   ne débloque jamais ce que l'org autorise.
-- (Les tables legacy vivent encore en base jusqu'au B2 — copiées au boot par _init,
--  DROP une fois ce code promu en prod : DB partagée canari/prod.)
CREATE TABLE IF NOT EXISTS connector_acl (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('org', 'group')),
    scope_id TEXT NOT NULL,       -- org.id / group.id en texte
    connector TEXT NOT NULL,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('group', 'user')),
    principal_id TEXT NOT NULL,   -- group_id (en texte) ou sub
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope_type, scope_id, connector, principal_type, principal_id)
);
"""

# coffre des credentials (ADR 0002/0033)
CREDENTIALS = """
-- Coffre unique des credentials per-entité (user OU org OU group) : clés API,
-- sessions linkedin/crunchbase, OAuth Google multi-compte, platform keys.
-- entity_id = sub (user) | orgs.id::text (org) | org_groups.id::text (group) ;
-- toujours requêter (entity_type, entity_id) ENSEMBLE. Secret chiffré par
-- enveloppe AES-256-GCM dans `secret_enc` (obligatoire — pas de colonne
-- plaintext) ; déchiffrement JIT dans resolve_api_key. meta JSONB pour les
-- satellites (user_agent, scopes…).
CREATE TABLE IF NOT EXISTS connector_credentials (
    entity_type TEXT NOT NULL,            -- 'member' | 'user' | 'org' | 'group' | 'platform' (ADR 0044 §F)
    entity_id   TEXT NOT NULL,            -- member:'org:sub' | user:sub | org/group:id::text | platform:label
    connector   TEXT NOT NULL,            -- nom de connecteur (registre)
    account     TEXT NOT NULL DEFAULT '', -- discriminant multi-compte ('' = mono ; ex. email Google)
    secret_enc  TEXT,                     -- enveloppe AES-256-GCM (obligatoire)
    secret_kind TEXT NOT NULL DEFAULT 'api_key',
    meta        JSONB NOT NULL DEFAULT '{}',
    set_by      TEXT,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ADR 0044 : l'entrée du coffre EST une instance de connecteur (config possédée).
    version     INTEGER NOT NULL DEFAULT 1,   -- verrou optimiste (B1) vs last-writer-wins
    share_down  JSONB NOT NULL DEFAULT '[]',  -- grantees des instances PLATFORM uniquement (§F) — le cran BYO « restreindre sous le niveau » est RETIRÉ (2026-07-08 : restreindre = poser l'instance au bon niveau)
    share_side  JSONB NOT NULL DEFAULT '[]',  -- EXTENSION : prêts NOMINATIFS à des pairs (liste de refs de principaux)
    share_mode  TEXT NOT NULL DEFAULT 'open', -- ADR 0044 §F : polarité du vide de share_down. 'open' = vide→sous-arbre (BYO) ; 'closed' = vide→personne (plateforme)
    PRIMARY KEY (entity_type, entity_id, connector, account)
);
CREATE INDEX IF NOT EXISTS idx_conn_cred_entity ON connector_credentials(entity_type, entity_id);
"""

# schémas d'outils mis en cache
SCHEMAS = """
-- Schéma OBSERVÉ des connecteurs (rédaction de champs) : squelette clés+types dérivé
-- des VRAIES réponses des tools (JAMAIS de valeurs/PII). Source de vérité du schéma
-- affiché dans l'UI de rédaction — les sorties connecteurs sont des passthrough d'API
-- tierces qu'on ne possède pas, donc le schéma juste = ce qui transite. Alimenté par
-- `FieldRedactionMiddleware` (squelette par service, fusion incrémentale).
CREATE TABLE IF NOT EXISTS connector_schemas (
    service TEXT PRIMARY KEY,
    schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
