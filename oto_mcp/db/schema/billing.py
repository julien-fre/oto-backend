"""DDL du domaine « billing » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# options offertes
OPTION_COMPS = """
-- Comps d'options admin (gratuit) — débloque une option de connecteur (ex. `unipile`
-- = messagerie hébergée) pour une entité user|org, accordée par un admin. `access.
-- has_option` débloque l'option ssi comp posé OU abonnement d'org actif dont le
-- plan inclut l'option (ADR 0043, cf. org_subscriptions plus bas). Cf.
-- docs/connector-model.md, couche 3. Entity-keyé (user|org).
CREATE TABLE IF NOT EXISTS option_comps (
    entity_type TEXT NOT NULL,        -- 'user' | 'org'
    entity_id   TEXT NOT NULL,        -- sub (user) ou org_id en texte (org)
    option      TEXT NOT NULL,        -- 'unipile', …
    granted_by  TEXT,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_type, entity_id, option)
);
"""

# abonnements et paiements (ADR 0043)
SUBSCRIPTIONS = """
-- Abonnement payant PAR ORG (ADR 0043) — miroir ET machine à états : la
-- récurrence est orchestrée maison (billing_runner rejoue les échéances MIT sur
-- le mandat Mollie, réconciliation par polling), le miroir restant PSP-agnostique
-- → ce miroir fait foi pour l'entitlement (2e source du seam access.has_option,
-- mapping plan→options en code). Un abonnement max par org. La résiliation/
-- impayé ferme l'entitlement, jamais les données.
CREATE TABLE IF NOT EXISTS org_subscriptions (
    org_id BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'mollie',
    customer_id TEXT,                       -- cst_xxx Mollie
    card_id TEXT,                           -- (legacy Stancer, inutilisé sous Mollie)
    sepa_id TEXT,                           -- (legacy Stancer, inutilisé sous Mollie)
    mandate_id TEXT,                        -- mdt_xxx (mandat Mollie, card OU SEPA — rejeu MIT)
    mandate_rum TEXT,                       -- mandateReference Mollie
    method TEXT NOT NULL DEFAULT 'card',    -- 'card' | 'sepa'
    plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- incomplete (mandat en attente) | active | past_due | canceled
    current_period_end TIMESTAMPTZ,
    next_billing_at TIMESTAMPTZ,
    grace_until TIMESTAMPTZ,                -- posé au passage past_due (grace 14 j)
    canceled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_org_subs_due
    ON org_subscriptions(next_billing_at) WHERE status IN ('active', 'past_due');

-- Journal des échéances/paiements d'abonnement (audit + UI billing + file de
-- réconciliation). AUCUNE donnée carte ici — seulement les ids Mollie.
-- `status` = statut Mollie observé (enum PaymentStatus) ; la file de
-- réconciliation = lignes non terminales (index partiel).
CREATE TABLE IF NOT EXISTS billing_payments (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                     -- initial | renewal | method_change
    amount INTEGER NOT NULL,                -- centimes RÉELLEMENT passés au PSP (TTC depuis #486)
    currency TEXT NOT NULL DEFAULT 'eur',
    -- Décomposition fiscale de la tentative (#486), figée à l'instant du débit.
    -- NULLABLES, et c'est le point : les deux encaissements antérieurs au 28/08/2026
    -- ont été débités du HT sans TVA, ils ne sont pas réécrits, et `amount_ht IS NULL`
    -- est ce qui les distingue d'une ligne calculée. `vat_rate_bps` est en POINTS DE
    -- BASE (2000 = 20 %) : un taux entier ne peut pas dériver en flottant, et une
    -- colonne NUMERIC ressortirait en `Decimal`, que le sérialiseur JSON refuse.
    amount_ht INTEGER,                      -- centimes hors taxes
    vat_rate_bps INTEGER,                   -- taux en points de base (2000 = 20,00 %)
    vat_amount INTEGER,                     -- centimes de TVA (amount - amount_ht)
    country_code TEXT,                      -- pays de facturation retenu (ISO-3166-1 alpha-2)
    vat_scheme TEXT,                        -- fr_ttc | reverse_charge | export
    payment_intent_id TEXT,                 -- tr_xxx (premier paiement, page hébergée)
    payment_id TEXT,                        -- tr_xxx (MIT rejoué / id résolu)
    customer_id TEXT,                       -- cst_xxx (le customer Mollie de CETTE tentative)
    status TEXT NOT NULL,                   -- statut Mollie observé
    attempt SMALLINT NOT NULL DEFAULT 1,    -- n° de tentative (retries du runner)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_billing_payments_org ON billing_payments(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_payments_open
    ON billing_payments(created_at) WHERE status NOT IN ('paid', 'failed', 'canceled', 'expired');
"""

# identité de facturation par org (#486, socle de la facture #488)
IDENTITIES = """
-- Identité de facturation d'une org (#486) — QUI paie, et sous quel régime de TVA.
-- Collectée AVANT le premier paiement : `billing.subscribe` refuse tant que le
-- minimum n'est pas là (raison sociale, pays, adresse), parce que le pays décide du
-- montant réellement débité et que la facture (#488) ne peut pas s'émettre sans lui.
-- Une ligne par org, remplacée en bloc (c'est un formulaire, pas un journal) ;
-- l'historique de ce qui a été facturé vit sur `billing_payments`, qui fige sa
-- décomposition fiscale au moment du débit et ne bouge plus si l'identité change.
CREATE TABLE IF NOT EXISTS billing_identities (
    org_id BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    legal_name TEXT NOT NULL,               -- raison sociale, telle qu'elle ira sur la facture
    country_code TEXT NOT NULL,             -- ISO-3166-1 alpha-2, MAJUSCULES (⚠️ la Grèce est GR ici, EL en TVA)
    vat_number TEXT,                        -- n° TVA intracom NORMALISÉ, préfixe compris ; NULL = non assujetti déclaré
    address_line TEXT,
    address_line2 TEXT,
    postal_code TEXT,
    city TEXT,
    billing_email TEXT,                     -- destinataire de la facture, si différent de l'admin
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
