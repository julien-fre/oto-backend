"""DDL du domaine « droits » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ.

**Le cœur porte des droits DÉCLARÉS, datés, relus à chaque usage** (ADR 0070 §7). Il
ne sait pas qui paie : un droit est une ligne posée par un producteur — le commerce, un
admin — et le cœur se contente de la relire. D'où ce fragment à part, hors de
`schema/billing.py` : le cœur ne dépend pas du commerce.

**Portée** : une ligne vaut pour l'org (`sub` NULL) ou pour une personne dans l'org
(`sub` posé). **Valeur** : un entier, jamais vide (oui/non = 1/0). **Clé** : une du
catalogue (`entitlements_catalogue`), vérifiée à la pose — pas par la base.

`source` est une étiquette OPAQUE pour le cœur : la règle d'application ne dépend pas
d'elle. Elle entre dans l'unicité pour que deux producteurs posent le même droit sans
s'écraser. Une ligne par (org, personne, droit, source) : `UNIQUE NULLS NOT DISTINCT`,
qui tient avec `sub` nul (PostgreSQL 15+), déclarée DANS le `CREATE TABLE` — aucun
ordre séparé à jouer au démarrage sur une base qui a déjà la table.

Base NEUVE : ce fragment. Base EXISTANTE (née de la révision 0004, clé primaire
`(org_id, right_key, source)`, `value` nullable) : révisions Alembic
`0014_droits_portee_personne` puis `0015_droits_valeur_obligatoire`.
"""
from __future__ import annotations

ORG_ENTITLEMENTS = """
-- Un droit déclaré (ADR 0070 §7). Valide à l'instant T ssi `starts_at <= T` et
-- (`expires_at` NULL ou `> T`) — le filtre vit en SQL, dans `db/entitlements.py`.
CREATE TABLE IF NOT EXISTS org_entitlements (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    -- NULL = ligne de l'org ; sinon la personne (sub) dans l'org.
    sub TEXT,
    right_key TEXT NOT NULL,
    -- Jamais vide : oui/non = 1/0, sinon le nombre (plafond, quota par jour).
    value INTEGER NOT NULL,
    -- Qui l'a posé, au sens du producteur : étiquette opaque, sans énumération.
    source TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULL = sans échéance.
    expires_at TIMESTAMPTZ,
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT org_entitlements_une_ligne
        UNIQUE NULLS NOT DISTINCT (org_id, sub, right_key, source)
);
"""
