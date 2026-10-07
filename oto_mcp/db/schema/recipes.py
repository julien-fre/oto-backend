"""DDL du domaine « recettes » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul.

**Une recette décrit comment des données passent d'un outil de connecteur à un
tableau, sans modèle** (`oto_recipe`, `docs/recettes.md`) : quel outil appeler, avec
quels arguments, comment parcourir ses pages, quel champ va dans quelle colonne. Un
agent l'écrit UNE fois ; le serveur l'exécute ensuite autant qu'on veut.

**Même découpe que les fonctions** (`schema/functions.py`) : `recipes` porte
l'IDENTITÉ et la version publiée, `recipe_versions` chaque VERSION, immuable une fois
écrite — corriger, c'est proposer la suivante. Seul son statut change.
"""
from __future__ import annotations

RECIPES = """
-- Une recette : l'identité et le pointeur vers la version publiée.
CREATE TABLE IF NOT EXISTS recipes (
    id BIGSERIAL PRIMARY KEY,
    -- Le propriétaire, comme toute ressource possédée (ADR 0049) : 'org' | 'user'.
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    -- La référence lisible, unique chez son propriétaire : c'est elle qu'une procédure
    -- cite. Elle ne se renomme pas.
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- La version qui s'exécute. NULL = rien de publié.
    published_version INTEGER,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_type, owner_id, slug)
);

-- Une version d'une recette. IMMUABLE une fois écrite : seul son statut change.
CREATE TABLE IF NOT EXISTS recipe_versions (
    id BIGSERIAL PRIMARY KEY,
    recipe_id BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    -- 'proposee' : écrite, pas encore éprouvée · 'publiee' : éprouvée sur une page
    -- réelle et mise en service · 'refusee' : gardée pour la trace.
    status TEXT NOT NULL DEFAULT 'proposee'
        CHECK (status IN ('proposee', 'publiee', 'refusee')),
    -- Le corps de la recette (outil, arguments, source, correspondance, clé, limites).
    body JSONB NOT NULL,
    note TEXT,
    proposed_by TEXT,
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    -- L'épreuve au moment de la publication : remplissage par colonne sur une page
    -- réelle. C'est le témoin auquel une exécution se compare.
    test_report JSONB,
    UNIQUE (recipe_id, version)
);
"""

#: Ce que l'EXÉCUTION d'une recette tient en base, au-delà de sa définition : le bail qui
#: empêche deux exécutions sur le même tableau, le travail asynchrone en attente d'une
#: recette `pull` (un lancement Apify payé ne se relance pas parce qu'un jeton s'est
#: perdu), et les exécutions programmées.
RECIPE_RUNS = """
-- Un bail par tableau : deux exécutions simultanées sur les mêmes lignes paieraient
-- deux fois ou créeraient deux fois la même fiche chez un tiers. Pris à l'entrée,
-- rendu à la sortie, repris par un autre s'il a expiré (processus mort).
CREATE TABLE IF NOT EXISTS recipe_leases (
    lock_key TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    until TIMESTAMPTZ NOT NULL
);

-- Le travail asynchrone en cours d'une recette `pull` (un lancement Apify), par jeu de
-- paramètres : une exécution le reprend au lieu d'en relancer un, payé, à côté.
CREATE TABLE IF NOT EXISTS recipe_pending_jobs (
    recipe_id BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    params_key TEXT NOT NULL,
    job JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (recipe_id, params_key)
);

-- Une exécution programmée : la recette publiée, ses paramètres et son tableau, au
-- nom de qui l'a posée (revérifié à chaque passage), dans son org.
CREATE TABLE IF NOT EXISTS recipe_schedules (
    id BIGSERIAL PRIMARY KEY,
    recipe_id BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    -- La version programmée : une autre version publiée ensuite (par un autre membre)
    -- ne tourne JAMAIS sous l'identité de qui a posé ce programme.
    version INTEGER NOT NULL,
    sub TEXT NOT NULL,
    org_id BIGINT,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    datastore TEXT NOT NULL,
    every_minutes INTEGER NOT NULL CHECK (every_minutes >= 15),
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    failures INTEGER NOT NULL DEFAULT 0,
    last_run_at TIMESTAMPTZ,
    last_receipt JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS recipe_schedules_due
    ON recipe_schedules (next_run_at) WHERE enabled;
"""
