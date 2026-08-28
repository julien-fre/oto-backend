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
-- ⚠️ PROJECTION TRANSITOIRE, VOUÉE AU DROP (#507). Cette table est l'ANCIENNE forme
-- de la trace : une ligne par (sub, doc) = la dernière version acceptée, écrasée à
-- chaque nouvelle acceptation. Elle n'est plus la source de vérité — le journal
-- `legal_acceptance_events` l'est — et PLUS AUCUNE LECTURE ne la consulte.
--
-- Pourquoi elle reste, et sa PK avec : prod et preprod partagent la MÊME base
-- (`docs/live-migrations.md`). Le code servi en production avant ce lot fait ici un
-- `INSERT … ON CONFLICT (sub, doc_slug) DO UPDATE` : retirer l'unicité casserait son
-- `POST /api/me/legal/accept` — le gate CGU de l'INSCRIPTION — pendant toute la
-- fenêtre qui sépare le déploiement preprod du tag de prod. Un historique et cette
-- unicité ne pouvant pas coexister, le journal est une table NEUVE, et celle-ci est
-- maintenue en écriture double DATÉE jusqu'à ce que la prod serve le code qui lit le
-- journal. C'est le patron « lot A additif » : rien qu'une ligne existante ne puisse
-- violer, et le lot se défait par un `drop`.
--
-- La source de vérité des docs (version/label/url) vit en CODE (`legal_docs.py`),
-- miroir de `oto-websites/web/src/legal` — cette table ne trace QUE le consentement.
CREATE TABLE IF NOT EXISTS legal_acceptances (
    sub TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, doc_slug)
);

-- JOURNAL des acceptations (#487) — la source de vérité, et la seule chose que les
-- gates LISENT. **Une ligne par ACCEPTATION**, jamais écrasée : la question « a-t-il
-- accepté la version courante ? » se pose à la ligne la plus RÉCENTE de chaque doc
-- (`db/legal.get_legal_acceptances`, DISTINCT ON).
--
-- Pourquoi un journal et pas un état : sur la projection ci-dessus, accepter les
-- CGV 2.0 EFFAÇAIT la trace de l'acceptation des CGV 1.0. Une acceptation prouvée par
-- une ligne mutable n'est pas une preuve — c'est le dernier état d'une preuve, et ce
-- qu'on doit pouvoir opposer, c'est « à telle date, depuis telle adresse, il a accepté
-- telle version ».
--
-- ⚠️ Aucune FK, ni vers `users`, ni vers `orgs` : une preuve de consentement ne se
-- supprime pas en cascade avec ce qu'elle documente. `org_id` est donc un entier nu
-- (l'org de session au moment de l'acceptation — le PAYEUR, ADR 0043), comme
-- `option_comps.entity_id`, et non une référence.
--
-- `context`/`ip`/`user_agent`/`org_id` NULS = acceptation RECOPIÉE de la projection
-- (`_init.py`), donc antérieure au journal : on ne sait pas d'où elle vient, et lui
-- inventer un contexte ferait mentir la trace là où elle sert de preuve.
CREATE TABLE IF NOT EXISTS legal_acceptance_events (
    id BIGSERIAL PRIMARY KEY,
    sub TEXT NOT NULL,
    org_id BIGINT,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    context TEXT,                                -- 'access' | 'purchase' | NULL
    ip TEXT,
    user_agent TEXT,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- La lecture du gate (`DISTINCT ON (doc_slug) … ORDER BY doc_slug, accepted_at DESC`)
-- et l'anti-jointure de la recopie au boot passent toutes deux par là.
CREATE INDEX IF NOT EXISTS idx_legal_events_dernier
    ON legal_acceptance_events (sub, doc_slug, accepted_at DESC, id DESC);

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
