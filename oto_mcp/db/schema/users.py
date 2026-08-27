"""DDL du domaine « users » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# identité (table racine)
USERS = """
-- Identité seule. Les credentials (clés API, sessions linkedin/crunchbase,
-- OAuth Google) vivent TOUS dans le coffre chiffré `connector_credentials`.
CREATE TABLE IF NOT EXISTS users (
    sub TEXT PRIMARY KEY,
    email TEXT,
    name TEXT,
    role TEXT NOT NULL DEFAULT 'member',  -- member | admin (opérateur) | super_admin
    avatar_url TEXT,
    -- Préférence de langue de l'UI dashboard ('en'|'fr'). NULL = pas de préférence
    -- explicite (le front retombe sur la langue du navigateur).
    locale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# fiche profil du compte
PROFILE = """
-- Fiche « situation avec oto » par utilisateur. `profile` = data model libre (qui est
-- l'user, son métier, ses objectifs, connecteurs voulus, ton…) entretenu au fil de l'eau
-- via `oto_profile` et relu à chaque session (injecté au handshake). Une ligne par sub,
-- créée à la 1re écriture. (L'onboarding n'est PAS un mode : c'est un projet, ADR 0032 §7.)
CREATE TABLE IF NOT EXISTS user_account_profile (
    sub TEXT PRIMARY KEY,
    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- (Ex-`user_agent_readme` : la note personnelle de l'utilisateur est un GUIDE
-- `scope='user', delivery='init'` depuis l'ADR 0042 — plus une table à elle. La
-- DDL est retirée ici ; le DROP de la table résiduelle suit au lot d'après.)

"""

# alias de sub (bascule de compte)
ALIASES = """
-- Bascule de tenant Logto (B1, otomata#35) : alias ancien_sub → nouveau_sub. Posé
-- par migrate_sub au 1er login d'un compte sur le nouveau tenant (merge par email).
-- Sert à canonicaliser les tokens encore émis par l'ancien tenant pendant le drain
-- (sinon un vieux token re-créerait le compte supprimé). Vide hors fenêtre de bascule.
CREATE TABLE IF NOT EXISTS sub_aliases (
    old_sub TEXT PRIMARY KEY,
    new_sub TEXT NOT NULL,
    migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
