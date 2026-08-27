"""DDL du domaine « visibility » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# bascules de visibilité d'outils
TOOL_TOGGLES = """
-- Visibilité scopée par org (ADR 0015) : org_id=0 = profil perso/global (aucune
-- org active), >0 = profil de cette org. Une identité par (sub, org_id).
CREATE TABLE IF NOT EXISTS user_disabled_tools (
    sub TEXT NOT NULL,
    org_id BIGINT NOT NULL DEFAULT 0,
    tool_name TEXT NOT NULL,
    disabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, org_id, tool_name)
);

-- Ensemble positif explicite : tools que l'user a activé alors qu'ils sont
-- masqués par défaut (DEFAULT_HIDDEN_TOOLS). Sans cette table, un tool
-- default-hidden ne pourrait jamais être rendu visible (le modèle de base
-- n'a qu'un ensemble négatif).
CREATE TABLE IF NOT EXISTS user_enabled_tools (
    sub TEXT NOT NULL,
    org_id BIGINT NOT NULL DEFAULT 0,
    tool_name TEXT NOT NULL,
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, org_id, tool_name)
);

-- Denylist de tools par ORG (gouvernance de visibilité, PAS une barrière de
-- sécurité — ADR 0031, même esprit que DEFAULT_HIDDEN_TOOLS) : l'org_admin masque
-- des tools SPÉCIFIQUES par défaut pour son org. `user_enabled_tools` (au-dessus)
-- lève TOUJOURS ce masquage — même échappatoire perso que le masqué-par-défaut
-- plateforme, un cran plus spécifique. Remplace l'ancienne baseline ALLOWLIST
-- org/équipe (retirée 2026-07-03, commit 3951a57) : ici on choisit ce qu'on
-- masque, tout le reste (y compris les tools futurs) reste visible par défaut.
-- Pas de FK (même convention que user_disabled_tools/connector_availability
-- ci-dessus — ces tables de préférence self-managing ne référencent pas leur
-- entité) ; évite aussi tout souci d'ordre de création vs org_groups (défini
-- plus bas dans ce fichier).
CREATE TABLE IF NOT EXISTS org_disabled_tools (
    org_id BIGINT NOT NULL,
    tool_name TEXT NOT NULL,
    disabled_by TEXT,
    disabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, tool_name)
);

-- Mirror au grain ÉQUIPE — un chef d'équipe masque un tool pour SON équipe.
-- Additif avec le denylist d'org (union à la lecture, jamais un retrait croisé) :
-- une équipe ne peut jamais RÉVÉLER un tool que l'org a masqué.
CREATE TABLE IF NOT EXISTS group_disabled_tools (
    group_id BIGINT NOT NULL,
    tool_name TEXT NOT NULL,
    disabled_by TEXT,
    disabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, tool_name)
);
"""
