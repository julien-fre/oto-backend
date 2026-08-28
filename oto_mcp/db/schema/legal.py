"""DDL du domaine « legal » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# acceptations et documents légaux
LEGAL = """
-- HISTORIQUE des acceptations de documents légaux (gate frontend LegalGate, gate
-- d'achat `billing.subscribe`). **Une ligne par ACCEPTATION**, jamais écrasée
-- (#487) : la question « a-t-il accepté la version courante ? » se pose à la ligne
-- la plus RÉCENTE de chaque doc (`db/legal.get_legal_acceptances`, DISTINCT ON).
--
-- Pourquoi un historique et pas un état : jusqu'au 2026-08-28 la table portait une
-- PK `(sub, doc_slug)` et l'écriture était un upsert — accepter les CGV 2.0
-- EFFAÇAIT la trace de l'acceptation des CGV 1.0. Une acceptation prouvée par une
-- ligne mutable n'est pas une preuve : c'est le dernier état d'une preuve, et ce
-- qu'on doit pouvoir opposer, c'est « à telle date, depuis telle adresse, il a
-- accepté telle version ».
--
-- La source de vérité des docs (version/label/url) vit en CODE (`legal_docs.py`),
-- miroir de `oto-websites/web/src/legal` — cette table ne trace QUE le consentement.
--
-- ⚠️ Aucune FK, ni vers `users`, ni vers `orgs` : une preuve de consentement ne se
-- supprime pas en cascade avec ce qu'elle documente. `org_id` est donc un entier nu
-- (celui de l'org de session au moment de l'acceptation — le PAYEUR, ADR 0043),
-- comme `option_comps.entity_id`, et non une référence.
CREATE TABLE IF NOT EXISTS legal_acceptances (
    id BIGSERIAL,
    sub TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    -- 'access' (inscription) | 'purchase' (achat). NULL = ligne ANTÉRIEURE au
    -- 2026-08-28, quand seul (sub, doc) était tracé : NULL veut dire « contexte non
    -- tracé », surtout pas « access » — lui en inventer un serait une reconstitution.
    context TEXT,
    org_id BIGINT,
    ip TEXT,
    user_agent TEXT,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- PK NOMMÉE, et pas `legal_acceptances_pkey` : la migration de `_init.py` fait
    -- `DROP CONSTRAINT IF EXISTS legal_acceptances_pkey` pour retirer l'ancienne PK
    -- `(sub, doc_slug)`, et ce nom par défaut viserait sinon la PK toute neuve d'une
    -- install vierge (piège nommé dans `docs/live-migrations.md`).
    CONSTRAINT legal_acceptances_event_pkey PRIMARY KEY (id)
);

-- Override PAR TENANT des métadonnées d'un doc légal (`legal_docs.CURRENT_DOCS`).
-- Un tenant tiers (Tulina…) a ses PROPRES CGU, pas les nôtres — absence de ligne
-- pour (tenant, slug) ⟹ le défaut plateforme s'applique tel quel (`legal_docs.
-- docs_for`). Lue en LIVE à chaque `/api/me/legal` (pas de cache, pas de boot) :
-- contrairement au registre d'émetteurs (`tenancy.py`, construit au boot), une
-- écriture ici prend effet au tour suivant, sans redémarrage. `tenant_slug` n'est
-- PAS une FK vers `tenants.slug` — un slug qualifie un sub sans jamais requérir
-- que son émetteur soit déjà déclaré (même ordre lâche que `tenancy.qualify`).
CREATE TABLE IF NOT EXISTS tenant_legal_docs (
    tenant_slug TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    label TEXT NOT NULL,
    url TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_slug, doc_slug)
);

"""
