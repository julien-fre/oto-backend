"""DDL du domaine « tenants » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# palier tenant (ADR 0052), créé avant `orgs`
TENANTS = """
-- Palier TENANT (ADR 0052) — l'étage d'identité entre la plateforme et l'org :
-- un émetteur dédié, des domaines, des orgs. Créé AVANT `orgs`, qui le référence
-- (`orgs.tenant_id`, posé en ALTER dans `_init`) : même contrainte d'ordre que
-- pour `orgs` ci-dessous, une FK vers une table non encore créée échoue sur une
-- base vierge.
--
-- ⚠️ COLLISION DE VOCABULAIRE, à ne pas confondre : `sub_aliases` (plus bas) parle
-- de « bascule de tenant » au sens **instance Logto** (auth.oto.zone → auth.oto.ninja).
-- Ici, un tenant est l'étage d'identité du PRODUIT — il PORTE un émetteur, il n'en
-- est pas un. Le tenant `oto` (id 1) est celui de tout l'existant : son sub reste
-- **nu**, donc aucune ligne n'est retouchée et rien n'est rechiffré (l'AAD du coffre
-- dérive du sub, cf. 0052).
CREATE TABLE IF NOT EXISTS tenants (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    -- (lot L2) L'ÉMETTEUR du tenant : le claim `iss` EXACT de ses jetons, donc la
    -- clé du registre `issuer → (tenant, verifier)`. UNIQUE — deux tenants sur un
    -- même émetteur rendraient la sélection ambiguë, et l'ambiguïté ici décide de
    -- QUI est l'appelant.
    -- ⚠️ **NULL pour le tenant `oto`**, par construction : son émetteur est l'env
    -- (`LOGTO_ENDPOINT`), donc DB-INDÉPENDANT — l'authentification canonique ne
    -- doit jamais dépendre d'une lecture de table. Une ligne qui redéclarerait cet
    -- émetteur est ignorée par le registre (le primaire gagne toujours).
    issuer TEXT UNIQUE,
    -- JWKS du tenant. NULL = dérivé `<issuer>/jwks` (convention Logto, la voie
    -- nominale d'ADR 0052 §5) ; renseigné pour un BYO-issuer qui le publie ailleurs.
    jwks_uri TEXT,
    -- Domaines servis pour ce tenant (liste de hosts). Posé ici pour le binding
    -- `host → tenant → (AS, audience)` du lot L3 (audience stricte + PRM Host-aware) :
    -- **rien ne le lit en L2**.
    hosts JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Préfixe des outils de la plateforme MONTRÉS aux comptes de ce tenant
    -- (`oto_doc` → `tulina_doc`, cf. `tool_alias`). NULL = les noms canoniques.
    -- DÉCLARÉ, jamais dérivé du slug : un renommage rompt les procédures et la prose
    -- déjà écrites du tenant, donc il se décide plutôt qu'il ne s'attrape.
    tool_prefix TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
