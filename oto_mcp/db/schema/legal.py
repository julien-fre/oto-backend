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
-- Acceptation des documents légaux par utilisateur (gate frontend LegalGate, ADR
-- billing/legal). Une ligne par (sub, doc) = la DERNIÈRE version acceptée ; un bump
-- de version dans `legal_docs.CURRENT_DOCS` la rend « outstanding » jusqu'à ré-accept.
-- La source de vérité des docs (version/label/url) vit en CODE (`legal_docs.py`),
-- miroir de `oto-websites/web/src/legal` — cette table ne trace QUE le consentement.
CREATE TABLE IF NOT EXISTS legal_acceptances (
    sub TEXT NOT NULL,
    doc_slug TEXT NOT NULL,
    version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, doc_slug)
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
