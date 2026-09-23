"""DDL du domaine « droits » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ.

**Le cœur porte des droits DÉCLARÉS par org, datés, relus à chaque usage** (ADR 0070
§7). Il ne sait pas qui paie : un droit est une ligne posée par un producteur — le
commerce, un admin, un partenaire — et le cœur se contente de la relire. D'où ce
fragment à part, hors de `schema/billing.py` : le cœur ne dépend pas du commerce.

`source` est une étiquette OPAQUE pour le cœur (essai, abonnement, don, partenaire…) :
aucune énumération n'est posée, chaque producteur nomme la sienne. Elle entre dans la
clé primaire pour que deux producteurs posent le même droit sans s'écraser — le droit
est vivant tant qu'UNE de ses lignes l'est.
"""
from __future__ import annotations

ORG_ENTITLEMENTS = """
-- Un droit déclaré d'une org (ADR 0070 §7). Vivant ssi `starts_at <= NOW()` et
-- (`expires_at` NULL ou futur) — le filtre vit en SQL, dans `db/entitlements.py`.
CREATE TABLE IF NOT EXISTS org_entitlements (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    right_key TEXT NOT NULL,
    -- Le droit CHIFFRÉ (membres max, quota…). NULL = pas d'avis.
    value INTEGER,
    -- Qui l'a posé, au sens du producteur : étiquette opaque, sans énumération.
    source TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULL = sans échéance.
    expires_at TIMESTAMPTZ,
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, right_key, source)
);
"""
