"""DDL du domaine « connectors » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# RBAC connecteur interne à l'org
ACL = """
-- RBAC connecteur — table UNIQUE `connector_acl` (chantier ACL, cadrage 10/07 :
-- fusion d'org_connector_access + group_connector_access ; le grain est une COLONNE
-- de scope, pas une table par grain). Sémantique INCHANGÉE par scope :
-- · scope 'org' (ADR 0025) : l'org_admin réserve un connecteur à un sous-ensemble de
--   son org. ≥1 ligne pour (scope, connector) ⟹ RESTREINT (deny-by-default) ; absence
--   ⟹ ouvert à tous les membres. principal = un groupe (department) ou un user. DUR :
--   enforced en visibilité + au call-time (access.require_connector_access) ;
--   l'escalade org_admin transcende (0044 §G). Ouvert par défaut = zéro disruption.
-- · scope 'group' (ADR 0012 B2, restrict-only) : narrowing pur de l'ACL d'org — le
--   principal est toujours un MEMBRE ('user', sub) ; l'équipe restreint davantage,
--   ne débloque jamais ce que l'org autorise.
-- (Les tables legacy vivent encore en base jusqu'au B2 — copiées au boot par _init,
--  DROP une fois ce code promu en prod : DB partagée canari/prod.)
CREATE TABLE IF NOT EXISTS connector_acl (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('org', 'group')),
    scope_id TEXT NOT NULL,       -- org.id / group.id en texte
    connector TEXT NOT NULL,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('group', 'user')),
    principal_id TEXT NOT NULL,   -- group_id (en texte) ou sub
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope_type, scope_id, connector, principal_type, principal_id)
);
"""

# coffre des credentials (ADR 0002/0033)
CREDENTIALS = """
-- Coffre unique des credentials per-entité (user OU org OU group) : clés API,
-- sessions linkedin/crunchbase, OAuth Google multi-compte, platform keys.
-- entity_id = sub (user) | orgs.id::text (org) | org_groups.id::text (group) ;
-- toujours requêter (entity_type, entity_id) ENSEMBLE. Secret chiffré par
-- enveloppe AES-256-GCM dans `secret_enc` (obligatoire — pas de colonne
-- plaintext) ; déchiffrement JIT dans resolve_api_key. meta JSONB pour les
-- satellites (user_agent, scopes…).
CREATE TABLE IF NOT EXISTS connector_credentials (
    entity_type TEXT NOT NULL,            -- 'member' | 'user' | 'org' | 'group' | 'platform' (ADR 0044 §F)
    entity_id   TEXT NOT NULL,            -- member:'org:sub' | user:sub | org/group:id::text | platform:label
    connector   TEXT NOT NULL,            -- nom de connecteur (registre)
    account     TEXT NOT NULL DEFAULT '', -- discriminant multi-compte ('' = mono ; ex. email Google)
    secret_enc  TEXT,                     -- enveloppe AES-256-GCM (obligatoire)
    secret_kind TEXT NOT NULL DEFAULT 'api_key',
    meta        JSONB NOT NULL DEFAULT '{}',
    set_by      TEXT,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ADR 0044 : l'entrée du coffre EST une instance de connecteur (config possédée).
    version     INTEGER NOT NULL DEFAULT 1,   -- verrou optimiste (B1) vs last-writer-wins
    share_down  JSONB NOT NULL DEFAULT '[]',  -- grantees des instances PLATFORM uniquement (§F) — le cran BYO « restreindre sous le niveau » est RETIRÉ (2026-07-08 : restreindre = poser l'instance au bon niveau)
    share_side  JSONB NOT NULL DEFAULT '[]',  -- EXTENSION : prêts NOMINATIFS à des pairs (liste de refs de principaux)
    share_mode  TEXT NOT NULL DEFAULT 'open', -- ADR 0044 §F : polarité du vide de share_down. 'open' = vide→sous-arbre (BYO) ; 'closed' = vide→personne (plateforme)
    PRIMARY KEY (entity_type, entity_id, connector, account)
);
CREATE INDEX IF NOT EXISTS idx_conn_cred_entity ON connector_credentials(entity_type, entity_id);
"""

# schémas d'outils mis en cache
SCHEMAS = """
-- Schéma OBSERVÉ des connecteurs (rédaction de champs) : squelette clés+types dérivé
-- des VRAIES réponses des tools (JAMAIS de valeurs/PII). Source de vérité du schéma
-- affiché dans l'UI de rédaction — les sorties connecteurs sont des passthrough d'API
-- tierces qu'on ne possède pas, donc le schéma juste = ce qui transite. Alimenté par
-- `FieldRedactionMiddleware` (squelette par service, fusion incrémentale).
CREATE TABLE IF NOT EXISTS connector_schemas (
    service TEXT PRIMARY KEY,
    schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# l'instance de connecteur comme OBJET (blueprint ADR 0053-D9, lot L6)
INSTANCES = """
-- L'INSTANCE de connecteur, OBJET (blueprint ADR 0053-D9, lot L6 — arbitrage **R1
-- prononcé par Alexis le 27/08 : « la table à côté »**).
--
-- **Pourquoi À CÔTÉ, et pas dans le coffre.** `credentials_store._aad` lie le
-- ciphertext aux QUATRE colonnes d'identité de SA ligne
-- (`connector_credentials:{entity_type}:{entity_id}:{connector}[:{account}]`), pas à
-- la table qui la porte. Une table posée à côté, la ligne du secret gardant ses
-- quatre colonnes, donne donc l'identifiant stable **sans un octet de
-- rechiffrement**. C'est ce qui a retiré R2 (le rechiffrement) de l'équation et
-- laissé R1 se décider sur ses vrais mérites : le modèle prévoit des instances SANS
-- secret — une instance `http` qui ne fige qu'une `base_url` et des champs (0057),
-- une sous-instance qui ne pose qu'une **détermination** (« le compte d'Alexandra »,
-- 0053-D9-3). Un objet qui n'est plus un credential n'a rien à faire dans une table
-- qui l'est.
--
-- **Le lien vers le coffre est la CLÉ à quatre colonnes portée ici**
-- (`owner_type`/`owner_id`/`connector`/`account` = `entity_type`/`entity_id`/
-- `connector`/`account` du coffre), et c'est une clé étrangère **LOGIQUE** — pas de
-- FK déclarée, délibérément : le coffre n'a AUCUNE clé de substitution (sa PK EST ce
-- quadruplet), donc rien à quoi un `credential_id` pourrait pointer ; et une vraie
-- FK interdirait le jour même les instances sans secret que le modèle attend.
-- Le sens du pointeur suit la même lecture : un `instance_id` ajouté SUR le coffre
-- serait perdu à chaque renommage (`credentials_store.rename_account` = `_upsert`
-- d'une ligne neuve + `_delete` de l'ancienne, et la liste de colonnes de l'INSERT
-- ne le nommerait pas), donc réparable seulement en éditant les primitives
-- d'écriture du coffre — exactement ce que « le coffre ne bouge pas » exclut.
--
-- ⚠️ **Rien ne lit encore cette table** : ni la cascade (`access.walk_cascade`), ni
-- la résolution (`access.resolve_credential`), ni le coffre. L'existant est NOMMÉ,
-- pas déplacé — l'intention est gardée par `tests/test_connector_instances_l6.py`,
-- pas par de la vigilance, et le premier lecteur de résolution devra en retirer le
-- garde-fou dans son propre commit.
CREATE TABLE IF NOT EXISTS connector_instances (
    -- L'identifiant STABLE, et la raison d'être du lot : il survit à ce qui casse un
    -- ref composé (renommage de label, de compte), et il donne aux sous-instances un
    -- parent DÉSIGNABLE. Sa forme de fil est `inst:{id}` (`instance_refs`).
    id BIGSERIAL PRIMARY KEY,
    connector TEXT NOT NULL,
    -- Le propriétaire, au vocabulaire du coffre + `tenant`, **prévu et INERTE** :
    -- l'entité `tenant` du coffre est le lot L-clés, pas celui-ci. `member` est le
    -- régime courant depuis 0033 ; `user` ne survit que pour les mounts OAuth.
    owner_type TEXT NOT NULL,
    -- `entity_id` du coffre, à l'octet près : sub (user) | 'org:sub' (member) |
    -- id::text (org/group) | label de la clé (platform, ADR 0044 §F).
    owner_id TEXT NOT NULL,
    -- ⚠️ **NOT NULL DEFAULT '' — et surtout pas nullable.** `''` est le marqueur
    -- mono-compte DU COFFRE (le compte nommé de #409 est une valeur, son absence
    -- est la chaîne vide) : le reprendre tel quel est ce qui rend le quadruplet
    -- comparable colonne à colonne. Une colonne nullable rendrait de surcroît
    -- l'index unique ci-dessous AVEUGLE (`NULL` n'entre en conflit avec rien), donc
    -- muet exactement sur les lignes qu'il existe pour protéger.
    account TEXT NOT NULL DEFAULT '',
    -- Nom donné par le propriétaire. **Posée VIDE par le lot** : le nom affiché
    -- reste DÉRIVÉ de `meta.label` du coffre (`connectors_instances._instance_name`).
    -- Le backfiller ici ferait un second domicile pour une donnée que personne ne
    -- lit encore — la recopie se fera dans le lot qui déplace la lecture.
    label TEXT,
    -- La config NON SECRÈTE appariée (ADR 0038). **Posée VIDE, elle aussi** : la
    -- config publique vit aujourd'hui dans `connector_credentials.meta`, et les
    -- `config_fields` packés (ex. `data_center` zoho) vivent DANS le ciphertext —
    -- les sortir est un lot à part entière (il faut déchiffrer pour dépacker).
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- R9, tranché le 27/08 : **la visibilité est une propriété de l'INSTANCE**,
    -- dérivée de la chaîne (découvrable par les scopes sous son propriétaire dans la
    -- MÊME org, jamais cross-org), avec surcharge explicite par le propriétaire.
    -- La colonne porte la SURCHARGE ; `inherited` (le défaut) dit « laisse la
    -- dérivation décider ». **La dérivation n'est pas écrite** — c'est un lot.
    visibility TEXT NOT NULL DEFAULT 'inherited',
    -- Sous-instances (0053-D9-3) : une instance enfant AJOUTE des crans à son parent
    -- — « le LinkedIn d'Alexandra » sous la clé Unipile plateforme. Rien n'en crée
    -- encore. ⚠️ Le jour où une sous-instance REDÉFINIT ce que `account` contient au
    -- lieu de s'y superposer, les lignes multi-comptes du coffre sont à rechiffrer
    -- (`account` EST une entrée d'AAD) : c'est le seul vrai candidat au
    -- rechiffrement, et il porte son propre lot (L6bis).
    parent_id BIGINT REFERENCES connector_instances(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Une instance s'ARCHIVE, elle ne se supprime pas — même doctrine que `grants`
    -- (0053-D7) : une consommation, un partage ou un binding qui la désignent
    -- doivent pouvoir la relire après son retrait.
    revoked_at TIMESTAMPTZ,
    -- POURQUOI elle a été archivée. Sans ce mot, un archivage est muet six mois plus
    -- tard : impossible de distinguer « l'utilisateur a retiré sa clé » (le cas normal)
    -- de « on a réparé une orpheline d'avant le lot » (un geste de maintenance, jamais
    -- une décision d'utilisateur). NULLABLE et sans CHECK : les valeurs sont posées par
    -- ce dépôt seul (`credential_removed`, `renamed_onto_existing`, `vault_row_missing`)
    -- et un vocabulaire fermé ici n'ajouterait qu'une migration au prochain motif.
    revoked_reason TEXT,
    -- Contraintes NOMMÉES (docs/live-migrations.md) : un futur DROP CONSTRAINT ne
    -- peut pas viser autre chose que ce qu'il croit viser.
    CONSTRAINT connector_instances_owner_type_check
        CHECK (owner_type IN ('platform', 'tenant', 'org', 'group', 'member', 'user')),
    CONSTRAINT connector_instances_visibility_check
        CHECK (visibility IN ('inherited', 'hidden', 'org'))
);
-- **Une instance vivante par ligne de coffre**, et c'est la base qui le tient, pas
-- le backfill : l'unicité porte sur le quadruplet du coffre. PARTIEL sur les
-- vivantes, pour qu'une instance archivée n'interdise pas à jamais d'en poser une
-- neuve sur la même ligne — le refus de RESSUSCITER, lui, est une garde du backfill
-- (`WHERE NOT EXISTS`, révoquées comprises), pas de l'index : même partage des rôles
-- qu'entre `idx_grants_grantee` et `db.grants.edge_exists` au lot L5.
-- Son préfixe `(owner_type, owner_id)` sert aussi les lectures par propriétaire — pas
-- de second index pour ça (derive don't duplicate).
CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_instances_vault
    ON connector_instances(owner_type, owner_id, connector, account)
    WHERE revoked_at IS NULL;
-- Le JUMEAU NON PARTIEL de l'index unique, et il n'est pas un doublon : le backfill
-- de boot demande « existe-t-il une instance pour cette ligne de coffre, **archivée
-- comprise** ? » — c'est ce qui l'empêche de RESSUSCITER une instance retirée à la
-- main entre deux boots (la leçon de `db.grants.edge_exists` au lot L5). Un index
-- partiel ne peut pas servir une requête qui n'a pas son prédicat : PostgreSQL
-- retomberait sur un parcours complet, une fois par ligne du coffre. Même partage
-- des rôles qu'entre `idx_grants_grantee` (partiel, la résolution) et
-- `idx_grants_resource_grantee` (non partiel, le comptage) — et la même consigne :
-- ⚠️ ne PAS l'« harmoniser » avec l'unique en lui ajoutant `WHERE revoked_at IS NULL`.
CREATE INDEX IF NOT EXISTS idx_connector_instances_vault_all
    ON connector_instances(owner_type, owner_id, connector, account);
-- Descendre aux sous-instances d'un parent (0053-D9-3). Table et index naissent
-- ENSEMBLE ⟹ leur place est ici et pas dans `_init` (piège « CREATE INDEX d'une
-- NOUVELLE colonne », docs/live-migrations.md).
CREATE INDEX IF NOT EXISTS idx_connector_instances_parent
    ON connector_instances(parent_id);
"""
