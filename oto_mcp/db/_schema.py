"""DDL du store (chaîne SQL unique appliquée par `_init.init_db`).

Extrait de l'ex-monolithe `db.py` (barreau 2). `CREATE TABLE IF NOT EXISTS` —
les évolutions de colonnes sur tables existantes vivent dans `_init.init_db`.
"""
from __future__ import annotations

_SCHEMA = """
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Palier organization (= périmètre / store serveur) — table RACINE, définie en
-- tête car des tables plus bas la référencent (`unipile_accounts` etc.) : sur une
-- base VIERGE, PostgreSQL crée les tables dans l'ordre du DDL et une FK vers une
-- table non encore créée échoue (`relation "orgs" does not exist`, #151). Détail
-- du palier (appartenance, credentials) au bloc org_members plus bas.
CREATE TABLE IF NOT EXISTS orgs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    logo_url TEXT,
    domain TEXT,
    industry TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usage (
    sub TEXT NOT NULL,
    tool TEXT NOT NULL,
    day DATE NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sub, tool, day)
);

-- Journal des appels MCP (monitoring admin). Une ligne par appel de tool,
-- posée par calllog.ToolCallLogger (succès comme échec). Schéma CANONIQUE
-- calllog (contrat inter-projets, domicile = socle otomata-mcp/logging.py ;
-- l'ex-lib otomata-calllog est décommissionnée, otomata-calllog#1).
-- Volumétrie bornée par un prune au boot (cf. prune_tool_calls + init_db).
-- `sub` nullable : les appels stdio local non authentifiés n'ont pas d'identité.
CREATE TABLE IF NOT EXISTS tool_calls (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    server TEXT NOT NULL DEFAULT 'oto',
    -- Discriminateur d'événement (ADR 0017, « un seul flux ») : 'mcp' = invocation
    -- d'outil MCP (le cas historique, défaut) ; 'rest' = appel /api/* ; 'connector'
    -- = échec/événement de résolution de credential ou de connexion connecteur ;
    -- 'protocol' = événement PROTOCOLAIRE MCP (handshake `initialize`) — mesure la
    -- cadence de re-handshake par client (`client_id`) et le churn de `session_id`,
    -- dont dépendent la visibilité des tools et l'injection des blocs A/C.
    -- `tool` porte alors l'identifiant d'événement (route REST, nom de provider,
    -- méthode protocolaire…).
    -- Les lectures du monitoring d'outils filtrent kind='mcp' pour rester iso.
    kind TEXT NOT NULL DEFAULT 'mcp',
    sub TEXT,
    email TEXT,
    tool TEXT NOT NULL,
    args JSONB,
    ok BOOLEAN NOT NULL DEFAULT TRUE,
    error TEXT,
    duration_ms INTEGER,
    -- Corrélation (ADR 0017, extension OTO-LOCALE — PAS dans le contrat canonique
    -- calllog/otomata-mcp) : session_id = session mcp transport (grossier) ; run_id =
    -- déroulé/run (fin, posé par run_start, stampé ici). NULL hors run.
    session_id TEXT,
    run_id TEXT,
    -- Org sous laquelle l'appel a été émis (seam current_org au moment du call,
    -- extension OTO-LOCALE) — scope EXACT du journal d'audit org (#67). NULL hors org.
    org_id BIGINT,
    -- Application OAuth cliente porteuse du grant (`azp`/`client_id` du JWT :
    -- claude.ai, Claude Code, ChatGPT… — extension OTO-LOCALE). Télémétrie par
    -- surface, jamais une frontière d'autz. NULL en REST/dev local.
    client_id TEXT,
    -- Event Sentry du traceback de CET appel (extension OTO-LOCALE) : posé quand
    -- `SentryToolErrorMiddleware` a capturé (donc uniquement sur une erreur de CODE
    -- — les 4xx amont/refus d'entrée sont droppés). Lien direct journal → traceback,
    -- fin du détour « chercher par user.id dans Sentry ». NULL partout ailleurs.
    sentry_event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_created_at ON tool_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_calls_sub ON tool_calls(sub);
CREATE INDEX IF NOT EXISTS idx_tool_calls_server_tool ON tool_calls(server, tool, created_at);
-- Lentilles d'activité du datastore (ADR 0046 b4) : corrélation par `ns_id` résolu.
-- Index d'EXPRESSION partiel — seules les lignes `data_*` portent un ns_id, donc
-- l'index reste petit et la lecture d'un tableau ne scanne plus tout le journal.
-- `args` existe depuis la création de la table (contrat calllog) : sûr ici, contrairement
-- aux colonnes ajoutées par ALTER (cf. bloc ci-dessous).
CREATE INDEX IF NOT EXISTS idx_tool_calls_ns ON tool_calls ((args->>'ns_id'), created_at DESC)
    WHERE args->>'ns_id' IS NOT NULL;
-- idx_tool_calls_run (run_id) ET idx_tool_calls_org (org_id) créés dans le bloc
-- ALTER de init_db, APRÈS leur ADD COLUMN : sur une table existante, CREATE TABLE
-- IF NOT EXISTS est un no-op donc ces colonnes n'existent pas encore ici (un index
-- les référençant dans _SCHEMA = crash UndefinedColumn au boot, vécu le 2026-06-25).

-- Signaux d'usage volontaires (ADR 0017, barreau 3) : feedback de l'agent/humain
-- sur un outil + cas d'usage non couverts (gap). DURABLE (hors prune 30j de
-- tool_calls) : c'est le signal qui pilote révisions d'outils/doctrines + backlog.
-- Le face-agent est AUSSI un tool_call (auto-journalisé, corrélé run_id) ; cette
-- table porte le CONTENU durable. Table neuve → indexes inline sûrs.
CREATE TABLE IF NOT EXISTS usage_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sub TEXT,
    org_id BIGINT,
    signal TEXT NOT NULL,        -- 'tool_feedback' | 'gap'
    kind TEXT NOT NULL,          -- feedback: bug|misleading_doc|wrong_result|praise|other ; gap: missing_tool|missing_doctrine|missing_data|other
    target TEXT,                 -- feedback: nom de l'outil ; gap: l'intention (ce qu'on voulait faire)
    body TEXT,                   -- description libre
    session_id TEXT,             -- corrélation session (face-agent) ; NULL côté humain
    source TEXT NOT NULL DEFAULT 'agent',  -- 'agent' (MCP) | 'human' (REST dashboard)
    resolved_at TIMESTAMPTZ,     -- NULL = ouvert ; date = signal traité
    resolved_by TEXT,            -- sub de l'opérateur ayant résolu
    resolution TEXT              -- note libre : ce qui a été fait
);
CREATE INDEX IF NOT EXISTS idx_usage_signals_signal ON usage_signals(signal, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_signals_target ON usage_signals(signal, target, created_at DESC);

-- Runs / déroulés (ADR 0017, amende le « state-only » du barreau 1-2) : la
-- métadonnée SÉMANTIQUE d'un run (label, doctrine, outcome) est désormais PERSISTÉE
-- — la pile session-scopée de `doctrine_run.py` reste la source du run ACTIF (pour
-- stamper `tool_calls.run_id`), mais elle meurt avec la conversation. Cette table
-- donne la trace durable « l'user a déroulé telle doctrine, terminée tel outcome »
-- → anticipation du contexte injecté (#50 bloc C) + boucle d'usage dashboard. Le
-- DÉTAIL des appels d'un run reste corrélé via `tool_calls.run_id`. Table neuve →
-- indexes inline sûrs. `org_id` NULL hors org ; `outcome` NULL = run encore ouvert.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    sub TEXT,
    org_id BIGINT,
    project_id BIGINT,                          -- projet actif GELÉ au start (ADR 0032 §5/§6, B3) ; NULL hors projet
    label TEXT NOT NULL,
    doctrine TEXT,                              -- slug de la doctrine nommée ; NULL = run ad-hoc
    outcome TEXT,                               -- done|abandoned|failed|blocked ; NULL = ouvert
    note TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_runs_sub_org ON runs(sub, org_id, started_at DESC);
-- idx_runs_project est créé dans `_init` APRÈS l'ADD COLUMN project_id : sur une table
-- `runs` préexistante, CREATE TABLE IF NOT EXISTS est un no-op → la colonne n'existe
-- pas encore ici, un index la référençant dans _SCHEMA crashe au boot (vécu 2026-06-30,
-- même gotcha que idx_tool_calls_run/org ci-dessus).

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

-- Datastore = spine natif PG (ADR 0016). `user_datastores` = registre de
-- namespaces ; les rows vivent dans `datastore_rows` (JSONB). Propriété portée par
-- `(owner_type, owner_id)` (ADR 0030 : user/org/group). Phase H (cadrage 10/07)
-- TERMINÉE : les reliques per-sub/Sheets (`sub`, `spreadsheet_id`, `owner_email`,
-- table `datastore_shares`) sont purgées du code (B1, promu prod) et DROPpées (B2).
-- ⚠️ Les INDEX sur owner_type/owner_id NE sont PAS créés ici : sur une base
-- existante, `CREATE TABLE IF NOT EXISTS` est un no-op et ces colonnes n'existent
-- pas encore quand `_SCHEMA` s'exécute (ajoutées plus bas par ALTER). Index +
-- contrainte d'unicité owner créés dans init_db APRÈS l'ALTER (couvre fresh ET existant).
CREATE TABLE IF NOT EXISTS user_datastores (
    id BIGSERIAL PRIMARY KEY,
    owner_type TEXT NOT NULL DEFAULT 'user',
    owner_id TEXT,
    namespace TEXT NOT NULL,
    -- Mode TYPÉ optionnel (ADR 0032 §6 / 0029) : NULL = table libre (colonnes
    -- découvertes des rows) ; sinon un schéma déclaré
    -- {fields:[{key,label?,type?,role?}]} où role ∈ title|badge|metric|status|
    -- qualif|note pilote le rendu en fiches. Soft : pas de validation à l'écriture.
    schema JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Rows du datastore : un dict JSONB par row (types préservés nativement, fin de
-- la sentinelle `__j:`). `_id`/`_created_at`/`_updated_at` = colonnes, le reste
-- des champs user dans `data`. CASCADE sur la suppression du namespace.
CREATE TABLE IF NOT EXISTS datastore_rows (
    ns_id BIGINT NOT NULL REFERENCES user_datastores(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- File de travail (ADR 0046 D) : bail posé par data_claim_next (SKIP LOCKED).
    -- NULL = libre ; claimed_until < NOW() = bail expiré (row recyclable). Libéré
    -- par data_release ou par l'entrée dans un état terminal du cycle de vie.
    claimed_by TEXT,
    claimed_until TIMESTAMPTZ,
    PRIMARY KEY (ns_id, row_id)
);

-- (`datastore_shares` — legacy remplacée par `resource_grants`, ADR 0030 — DROPpée
--  en Phase H B2, 10/07.)

-- Projet = couche d'organisation (modèle produit 2026-06-27). Conteneur de travail
-- POSSÉDÉ (owner_type/owner_id, ADR 0030) : nom + brief (doc d'entrée inline pour
-- l'instant ; le Doc arborescent + les liens vers tableaux/procédures/connecteurs/
-- bases = incréments suivants). Partage/transfert via resource_grants
-- (resource_type='project'). `archived_at` = soft-delete. Table fraîche → index posé
-- inline (les colonnes existent dès le CREATE, contrairement à user_datastores).
CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    owner_type TEXT NOT NULL DEFAULT 'user',
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    -- Emoji facultatif : repère visuel du projet (listes, sidebar, en-tête).
    -- NULL = pas d'icône, l'UI retombe sur son rendu par défaut.
    icon TEXT,
    brief_md TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    is_template BOOLEAN NOT NULL DEFAULT FALSE,
    -- Publication d'un projet en endpoint MCP dédié `<mcp_slug>.mcp.oto.cx` (ADR 0032,
    -- amende #44). `mcp_access` ∈ {off (défaut, non publié) | anonymous (aucun login,
    -- toolset figé servi par la clé de l'org propriétaire) | org (JWT Logto, épingle
    -- l'org)}. `mcp_tools` = allowlist figée du preset (les seuls tools exposés sur le
    -- sous-domaine). `mcp_slug` UNIQUE = le label de sous-domaine (regex ^[a-z0-9-]{3,}$).
    mcp_slug TEXT UNIQUE,
    mcp_access TEXT NOT NULL DEFAULT 'off',
    mcp_tools TEXT[] NOT NULL DEFAULT '{}',
    -- Opt-in : exposer les tools `data_*` (datastore de l'org propriétaire) sur un
    -- endpoint `secret` sans login — l'endpoint agit alors sous l'autorité de l'org
    -- propriétaire. Défaut FALSE (datastore privé) ; jamais honoré en `anonymous`.
    mcp_expose_datastore BOOLEAN NOT NULL DEFAULT FALSE,
    -- Opt-in ADDITIONNEL, séparé de la lecture (#193) : autoriser l'ÉCRITURE du datastore
    -- (data_write/data_set_schema) sur l'endpoint partagé. Défaut FALSE (lecture seule).
    mcp_expose_datastore_write BOOLEAN NOT NULL DEFAULT FALSE,
    -- Opt-in : exposer les PAGES du projet (oto_doc en lecture) sur un endpoint
    -- `secret`. Séparé du datastore : les pages portent des notes internes.
    mcp_expose_docs BOOLEAN NOT NULL DEFAULT FALSE,
    -- Prose servie au DESTINATAIRE de l'endpoint publié (le client qui branche l'URL).
    -- Distincte de `brief_md`, qui est interne (gotchas, arbitrages, noms) : publier le
    -- brief tel quel serait une fuite. NULL = pas de guidage publié.
    mcp_instructions_md TEXT,
    -- Projet forké depuis un partage public (« Ajouter à mon Oto ») : pointeur vers la
    -- source, pour un import IDEMPOTENT par org (idx_projects_copied_from, créé dans `_init`
    -- après l'ADD COLUMN — même gotcha que is_template sur une table préexistante).
    copied_from BIGINT,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_type, owner_id);
-- ADR 0032 §7 (B5a) : un projet publié comme MODÈLE (template) est copiable (op=copy).
-- idx_projects_template est créé dans `_init` APRÈS l'ADD COLUMN is_template (même
-- gotcha que idx_runs_project : table projects préexistante → colonne absente ici).

-- Liens d'un Projet vers les entités qu'il regroupe (incrément 2). Pointeur TYPÉ,
-- pas un FK cross-store : `target_type` ∈ {tableau, procedure, connecteur, base} et
-- `target_ref` = l'id/slug/nom dans le store d'origine (datastore.id, doctrine slug,
-- connecteur name). `label` dénormalisé pour l'affichage ; `role`
-- = pourquoi cette entité est ici / ce qu'elle apporte au projet — le « pourquoi » vit
-- sur le LIEN, pas sur l'entité (ADR 0032 §2). Le caractère cross-projet n'est PAS
-- stocké : il est DÉRIVÉ (même (target_type, target_ref) dans ≥2 projets). `config` =
-- surcharge contextuelle PRÉFAITE de l'entité dans CE projet (ADR 0032 §4, B2) — pour
-- un `connecteur` : {identity_id?, instructions_md?} (quel compte + instructions de
-- surcharge en prose, lues par l'agent au chargement, jamais déclarées à la volée).
-- CASCADE sur la suppression du projet ; unicité (projet, type, ref) → lien idempotent.
CREATE TABLE IF NOT EXISTS project_links (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    label TEXT,
    role TEXT,
    -- ADR 0035 (B2) : nom de slot BINDÉ par ce lien — vocabulaire DU PROJET (unicité
    -- (project_id, slot) via index partiel, posé dans le bloc ALTER d'init_db).
    slot TEXT,
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, target_type, target_ref)
);
CREATE INDEX IF NOT EXISTS idx_project_links_project ON project_links(project_id);

-- Doc = page markdown d'un projet, en ARBRE (incrément 3). `parent_id` NULL = page
-- de 1er niveau sous le projet (le `brief_md` du projet reste la page d'entrée, pas
-- une ligne ici). `kind` ∈ {doc (humain), note (agent), source (import)}. CASCADE sur
-- la suppression du projet ET du parent (sous-arbre). Pas d'ownership propre : un Doc
-- hérite de l'accès de SON projet (ownership.can_access sur le projet).
CREATE TABLE IF NOT EXISTS docs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'doc',
    -- Lot 3 Ship 2 : chapô (sous-titre curé, fallback dérivé à la lecture) + ordre
    -- curé de la fratrie (entiers espacés ×16, réindexés atomiquement au move).
    description TEXT,
    position INTEGER,
    -- Partage public (gap #4a) : NULL = privé ; sinon un token aléatoire qui sert
    -- de lien public en lecture seule (/api/public/docs/{token}). Index unique créé
    -- dans `_init` après l'ADD COLUMN (jamais ici — table docs préexistante).
    public_token TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_docs_project ON docs(project_id);
CREATE INDEX IF NOT EXISTS idx_docs_parent ON docs(parent_id);

-- Historique de versions d'un Doc (ADR 0032 §3, B4c) : à chaque mise à jour, l'état
-- ANTÉRIEUR (title + body_md) est snapshotté ici avant écriture → chaîne de versions
-- consultable. `edited_by` = qui a posé la nouvelle version (a remplacé ce snapshot).
-- CASCADE sur la suppression du doc. Pas de revue/validation (auto-accept, cf. réunion).
CREATE TABLE IF NOT EXISTS doc_revisions (
    id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    edited_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_doc_revisions_doc ON doc_revisions(doc_id, created_at DESC);

-- Demandes de modification d'un Doc (ADR 0032 §3, gap #4b réunion 30/06) : un
-- utilisateur en LECTURE SEULE propose un nouveau contenu ; le propriétaire (write)
-- accepte (→ applique via update_doc, qui snapshotte la version courante) ou refuse.
-- `status` ∈ pending|accepted|rejected. CASCADE sur la suppression du doc.
CREATE TABLE IF NOT EXISTS doc_change_requests (
    id BIGSERIAL PRIMARY KEY,
    -- Ship 3 : `doc_id` NULL = proposition de CRÉATION (la page n'existe pas encore) ;
    -- `project_id` porte alors le projet cible + `proposed_parent_id`/`proposed_kind`.
    doc_id BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    project_id BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    proposed_parent_id BIGINT REFERENCES docs(id) ON DELETE SET NULL,
    proposed_kind TEXT,
    requested_by TEXT,
    proposed_title TEXT,
    proposed_body_md TEXT NOT NULL DEFAULT '',
    message TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT dcr_target CHECK (doc_id IS NOT NULL OR project_id IS NOT NULL)
);

-- Backlinks [[…]] (lot 3 Ship 4) — graphe LÉGER de pages qui se citent. Table
-- DÉRIVÉE (reconstructible par re-parse des bodies) : `from_doc` cite `to_doc`.
-- Résolue contre le projet courant + la KB de l'org (précédence projet > KB) ;
-- purgée en cascade au delete d'un des deux docs.
CREATE TABLE IF NOT EXISTS doc_links (
    from_doc BIGINT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    to_doc   BIGINT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    PRIMARY KEY (from_doc, to_doc)
);
CREATE INDEX IF NOT EXISTS idx_doc_links_to ON doc_links(to_doc);

-- Embeddings des pages (lot 3, recherche sémantique V2) — une ligne par doc,
-- mistral-embed 1024 en halfvec. `content_sha` = idempotence (ré-embed seulement si
-- le texte change). Table NEUVE → l'index HNSW ici est sûr (créée juste au-dessus).
CREATE TABLE IF NOT EXISTS doc_embeddings (
    doc_id BIGINT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    content_sha TEXT NOT NULL,
    embedding halfvec(1024) NOT NULL,
    model TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_doc_embeddings_hnsw
    ON doc_embeddings USING hnsw (embedding halfvec_cosine_ops);

-- Embeddings des sources NON-page (oto/#6 C) : briefs de projet + guides on-demand.
-- Table générique keyée (kind, ref) — ref = projects.id (brief) | guides.id (guide) ;
-- même modèle 1024d que doc_embeddings. Le worker draine `embed_dirty` de projects/guides.
CREATE TABLE IF NOT EXISTS aux_embeddings (
    kind TEXT NOT NULL,                          -- 'brief' | 'guide'
    ref BIGINT NOT NULL,                         -- projects.id | guides.id
    content_sha TEXT NOT NULL,
    embedding halfvec(1024) NOT NULL,
    model TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_aux_embeddings_hnsw
    ON aux_embeddings USING hnsw (embedding halfvec_cosine_ops);

-- Chunks de DÉBORDEMENT d'une page longue (oto/#6 C) : au-delà du 1er morceau (16k,
-- dans doc_embeddings), les chunks 1..N vivent ici → toute la page est recherchable.
-- Additif : doc_embeddings inchangé (rétro-compat). CASCADE sur la page.
CREATE TABLE IF NOT EXISTS doc_chunk_embeddings (
    doc_id BIGINT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content_sha TEXT NOT NULL,
    embedding halfvec(1024) NOT NULL,
    model TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_embeddings_hnsw
    ON doc_chunk_embeddings USING hnsw (embedding halfvec_cosine_ops);

-- Embeddings des LIGNES de datastore (#67 V2.2) — sémantique OPT-IN par namespace
-- (flag `user_datastores.semantic_search`). Une ligne = un vecteur (JSON rendu), même
-- modèle 1024d. Le worker draine `datastore_rows.embed_dirty` des namespaces opt-in.
-- Table NEUVE → HNSW sûr ici. CASCADE sur la row (FK composite sur sa PK).
CREATE TABLE IF NOT EXISTS datastore_row_embeddings (
    ns_id BIGINT NOT NULL,
    row_id TEXT NOT NULL,
    content_sha TEXT NOT NULL,
    embedding halfvec(1024) NOT NULL,
    model TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ns_id, row_id),
    FOREIGN KEY (ns_id, row_id) REFERENCES datastore_rows(ns_id, row_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_datastore_row_embeddings_hnsw
    ON datastore_row_embeddings USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_doc_change_requests_doc ON doc_change_requests(doc_id, status, created_at DESC);

-- Journal d'activité d'un projet (incrément 5) : qui a fait quoi, quand. Alimenté
-- best-effort par les capacités projet/doc sur les mutations. `action` = verbe court
-- (project.create, doc.update…), `detail` = libellé libre.
CREATE TABLE IF NOT EXISTS project_activity (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sub TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_project_activity_project ON project_activity(project_id, created_at DESC);

-- Fichiers bruts d'un projet — carte « Autre document » (ADR 0032 §3). PDF/HTML/etc.
-- stockés en Object Storage DURABLE+privé (media_store.upload_object → `s3_key`
-- persistée, presigned à la lecture). `title`/`description` = la coquille légère
-- décrite en réunion (consommable par l'agent) ; `summary` = résumé IA, rempli plus
-- tard. CASCADE sur la suppression du projet ; pas d'ownership propre (hérite du projet).
CREATE TABLE IF NOT EXISTS project_files (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    s3_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT,
    size_bytes BIGINT,
    title TEXT,
    description TEXT,
    summary TEXT,
    public BOOLEAN NOT NULL DEFAULT FALSE,    -- partagé publiquement (ACL public-read, ADR 0032 §3)
    public_url TEXT,                          -- URL publique permanente quand public ; NULL sinon
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_project_files_project ON project_files(project_id);

-- Primitive de ressource possédée (ADR 0030). Partage cross-type deny-by-default :
-- une ressource est identifiée par (resource_type, resource_id) ; chaque ligne
-- accorde une permission à un principal (user/group/org). L'OWNER de la ressource
-- vit sur la ressource elle-même (colonnes owner_type/owner_id), PAS ici — derive
-- don't duplicate. `resource_id` = l'id STABLE de la ressource (ex.
-- user_datastores.id::text), pas un nom (survit au renommage). Résolu par le seam
-- `ownership.py` (plan contenu can_access = owner∪grants ; plan gouvernance
-- can_govern = owner∪escalade roles.py).
CREATE TABLE IF NOT EXISTS resource_grants (
    resource_type TEXT NOT NULL,                               -- 'datastore_namespace' | …
    resource_id TEXT NOT NULL,                                 -- id stable de la ressource
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group', 'org')),
    principal_id TEXT NOT NULL,                                -- sub | group_id | org_id (texte)
    -- ADR 0048 : le grant porte un RÔLE (lecteur/éditeur/gérant). `permission` (read/write)
    -- reste la projection CONTENU appariée (viewer→read, editor/manager→write) — inchangée
    -- pour tout le SQL du plan contenu (max(g.permission)/g.permission='write') ; `role`
    -- porte en plus la GOUVERNANCE grantable (`manager` → can_govern). Source de vérité = role.
    permission TEXT NOT NULL DEFAULT 'write' CHECK (permission IN ('read', 'write')),
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('viewer', 'editor', 'manager')),
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (resource_type, resource_id, principal_type, principal_id)
);
CREATE INDEX IF NOT EXISTS idx_resource_grants_principal
    ON resource_grants(principal_type, principal_id, resource_type);

-- ─────────── L'accès par CHAÎNE de grants (blueprint ADR 0053, lot L4) ───────────
-- ⚠️ NE PAS CONFONDRE avec `resource_grants` juste au-dessus. Celle-là partage un
-- CONTENU (0030/0048 : un tableau, un projet, une page) à un principal, à PLAT — une
-- ligne, un droit. `grants` porte un autre plan : le droit d'UTILISER une ressource
-- opérante (v1 : une instance de connecteur), par une chaîne MATÉRIALISÉE qui
-- descend de la plateforme jusqu'à l'utilisateur en ne faisant que resserrer
-- (0053-D4/D5 : « min des contraintes le long de la chaîne »). Les deux coexistent
-- tant que la bascule (L5+) n'a pas eu lieu.
--
-- ⚠️ **Écrites par rien, lues par rien — et c'est le lot.** Le socle arrive AVANT
-- son premier usage : c'est ce qui rendra la bascule faisable connecteur par
-- connecteur (la clé plateforme d'abord) plutôt qu'en un grand soir. L'intention
-- est gardée par `tests/test_grants_l4_migration.py`, pas par de la vigilance : le
-- premier lecteur devra retirer ce test dans son propre commit.
CREATE TABLE IF NOT EXISTS grants (
    id BIGSERIAL PRIMARY KEY,
    -- La ressource accordée. `connector_instance` en v1 ; le champ EST le point
    -- d'extension prévu par 0053 §4 (« extensible : 'capability', … »).
    resource_kind TEXT NOT NULL DEFAULT 'connector_instance',
    -- ⚠️ ÉCART ASSUMÉ avec la forme du banc, qui portait `resource_id BIGINT` (son
    -- unique `resource_kind` étant une instance, donc un BIGSERIAL). Ici TEXT, pour
    -- la même raison que `resource_grants.resource_id` : dès que `resource_kind`
    -- varie, la forme de l'id varie avec lui. Un BIGINT n'aurait fermé le champ
    -- d'extension qu'en apparence — il aurait forcé le premier `resource_kind` non
    -- numérique à migrer la colonne.
    resource_id TEXT NOT NULL,
    -- Qui accorde, à qui. Le scope est POLYMORPHE (0053-D3 : « la plateforme est un
    -- scope propriétaire comme les autres ») ⟹ pas de clé étrangère possible ; le
    -- vocabulaire des cinq scopes est fermé par un CHECK, faute de FK.
    grantor_kind TEXT NOT NULL,
    -- ⚠️ TEXT, et pour la raison déjà écrite sur `nodes.owner_id` : un scope `user`
    -- est un `sub` Logto (`users.sub` EST la clé primaire, il n'existe aucun id
    -- numérique d'utilisateur) et `platform` n'a pas d'id du tout — la convention
    -- maison est la chaîne littérale 'platform' (cf. `guides.owner_id`). Un BIGINT
    -- obligerait à inventer un surrogate par utilisateur : une migration d'identité,
    -- sans rapport avec le modèle d'accès.
    grantor_id TEXT NOT NULL,
    grantee_kind TEXT NOT NULL,
    grantee_id TEXT NOT NULL,
    -- Les bornes portées par l'arête (0053-D4). Vocabulaire **FERMÉ** à cinq
    -- entrées : role | quota | budget | rate | expiration. (`périmètre` en a été
    -- retiré le 08/08 : un seul emploi réel, aucun cas qui le réclamait.) La
    -- fermeture est MÉCANIQUE — cf. le CHECK en pied de table — donc l'étendre est
    -- une migration, ce qui EST le sens de « extensible seulement par amendement ».
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- La chaîne matérialisée, jamais reconstruite par inférence (0053-D4).
    -- NULL = grant racine du propriétaire. La révocation d'un parent emporte la
    -- descendance À LA LECTURE (la marche voit `revoked_at`), sans rien propager :
    -- c'est l'argument du banc contre tout cache (une racine touche 1 998 users).
    parent_id BIGINT REFERENCES grants(id),
    -- D'où vient l'arête : 'manual' | 'subscription:<x>' | 'template:<x>' | 'promo'.
    -- ⚠️ Le chemin d'appel ne LIT JAMAIS ce champ (0053-D6, premier interdit) : à
    -- l'évaluation un grant est un grant. Il n'existe que pour le producteur, qui
    -- diffe les grants DONT IL EST LA SOURCE (second interdit) — un webhook du PSP ne
    -- doit jamais reprendre ce qu'un admin a accordé à la main.
    source TEXT NOT NULL DEFAULT 'manual',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 0053-D7 : un grant révoqué ou remplacé s'ARCHIVE, il ne se supprime jamais —
    -- sinon un simple changement de plan efface la consommation, et personne ne le
    -- voit. C'est aussi pourquoi `grant_counters` ne porte PAS de ON DELETE CASCADE.
    revoked_at TIMESTAMPTZ,
    -- Contraintes NOMMÉES (docs/live-migrations.md) : un futur DROP CONSTRAINT ne
    -- peut pas viser autre chose que ce qu'il croit viser.
    CONSTRAINT grants_grantor_kind_check
        CHECK (grantor_kind IN ('platform', 'tenant', 'org', 'group', 'user')),
    CONSTRAINT grants_grantee_kind_check
        CHECK (grantee_kind IN ('platform', 'tenant', 'org', 'group', 'user')),
    -- Le vocabulaire fermé, rendu mécanique : ôter les cinq clés connues doit laisser
    -- l'objet VIDE. Attrape aussi le cas où `constraints` n'est pas un objet.
    CONSTRAINT grants_constraints_vocabulary CHECK (
        constraints - ARRAY['role', 'quota', 'budget', 'rate', 'expiration'] = '{}'::jsonb)
);
-- TROIS index, et ce sont des CONDITIONS, pas des détails (banc L0 du 07/08,
-- `oto-blueprint:docs/banc-resolution-grants.md`). Table et index naissent ensemble
-- ⟹ leur place est ici et pas dans `_init` (piège « CREATE INDEX d'une NOUVELLE
-- colonne », docs/live-migrations.md).
--
-- 1. Trouver les feuilles : la marche part TOUJOURS du bénéficiaire, et c'est ce qui
--    la rend plate (×134 de grants ⟹ +42 % de latence seulement).
--    (Le banc portait un `resource_kind` en 3e position, qu'aucune de ses requêtes
--    n'utilisait ; l'ADR et le plan retiennent les deux colonnes utiles.)
CREATE INDEX IF NOT EXISTS idx_grants_grantee
    ON grants(grantee_kind, grantee_id) WHERE revoked_at IS NULL;
-- 2. Remonter la chaîne jusqu'à la racine (profondeur bornée, 4 en pratique).
CREATE INDEX IF NOT EXISTS idx_grants_parent ON grants(parent_id);
-- 3. COMPTER. ⚠️⚠️ **Celui-ci est une CONDITION, pas une optimisation, et il doit
--    rester NON PARTIEL.** Mesuré : sans lui la lecture d'un quota coûte **73,8 ms**
--    (p99 104,5), avec lui **0,035 ms** — ×2000. Sur un serveur MONO-LOOP, 74 ms par
--    appel compté est un gel de boucle, le mode de panne exact que
--    `docs/event-loop-perf.md` documente déjà. La cause est structurelle et ne
--    s'améliorera pas : une clé mutualisée est un MOYEU (~52 000 arêtes entrantes en
--    moyenne, 68 364 au maximum à l'échelle mesurée), et sommer ses arêtes sans cet
--    index, c'est toutes les traverser pour n'en garder que deux ou trois.
--    ⚠️ **Ne PAS lui ajouter `WHERE revoked_at IS NULL` pour l'« harmoniser » avec
--    l'index 1.** Les deux lectures sont opposées : la résolution ignore les arêtes
--    révoquées, le comptage de D7 les compte — un quota se lit en sommant les arêtes
--    de la même (instance, bénéficiaire, fenêtre), **archivées comprises**, sans quoi
--    une bascule de plan remet le compteur à zéro. Un index partiel ne peut pas
--    servir une requête sans ce prédicat : PostgreSQL retomberait sur un scan, et les
--    74 ms reviendraient **en silence**, sans une ligne de diff qui les explique.
CREATE INDEX IF NOT EXISTS idx_grants_resource_grantee
    ON grants(resource_id, grantee_kind, grantee_id);

-- Le metering s'attache à l'ARÊTE (0053-D7) : la consommation free-tier se compte
-- contre le grant plateforme→org, et le plafond d'un partenaire white-label est le
-- même compteur un étage plus haut — aucune feature dédiée. L'arête porte la règle
-- et les incréments ; le PLAFOND, lui, s'évalue sur le couple (instance,
-- bénéficiaire) en sommant les arêtes (cf. l'index 3 ci-dessus).
--
-- ⚠️ Pas de `ON DELETE CASCADE` sur la FK, et c'est délibéré : D7 pose qu'un grant
-- s'archive et ne se supprime JAMAIS. Sans CASCADE, supprimer un grant déjà compté
-- devient impossible (la FK refuse) au lieu d'effacer silencieusement l'historique
-- de consommation — la règle est tenue par la base, pas par la mémoire du lecteur.
--
-- ⚠️ La colonne s'appelle `window_start` et non `window` : WINDOW est un mot RÉSERVÉ
-- de PostgreSQL (fonctions de fenêtrage), inutilisable sans guillemets — ce que le
-- banc avait déjà tranché ainsi.
--
-- Non mesuré, et à ne pas confondre avec le coût de LECTURE ci-dessus : le DÉBIT en
-- écriture, dont le risque est une CONTENTION (l'incrément tombe sur une ligne
-- partagée par tous les bénéficiaires sous l'arête, que PostgreSQL sérialise) et non
-- une latence. Il dépend d'un arbitrage non rendu — débiter la feuille, ou chaque
-- arête traversée.
CREATE TABLE IF NOT EXISTS grant_counters (
    grant_id BIGINT NOT NULL REFERENCES grants(id),
    window_start DATE NOT NULL,
    calls BIGINT NOT NULL DEFAULT 0,
    spend NUMERIC NOT NULL DEFAULT 0,
    CONSTRAINT grant_counters_pkey PRIMARY KEY (grant_id, window_start)
);

CREATE TABLE IF NOT EXISTS user_api_tokens (
    id BIGSERIAL PRIMARY KEY,
    sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT 'cli',
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,  -- NULL = non-expirant (token CLI long-lived). Sinon rejeté passé l'échéance.
    -- Portée du jeton (`token_scopes.py`). NULL = jeton NON PORTÉ : il est le sub,
    -- pleins pouvoirs (comportement historique de tous les jetons émis à ce jour).
    -- Non NULL = deny-by-default : seules les routes que la portée nomme passent,
    -- p.ex. {"namespaces": {"leads-dormants": "read"}} pour une intégration tierce.
    scopes JSONB
);
CREATE INDEX IF NOT EXISTS idx_user_api_tokens_sub ON user_api_tokens(sub);

-- Jetons d'upload signés à USAGE UNIQUE (issue oto-backend#105). Un `oto_upload_url`
-- rend une URL signée HMAC (payload scellé sub/org/cible + TTL) sur laquelle un agent
-- PUT du contenu volumineux hors-bande. Le jeton lui-même est STATELESS ; on ne
-- persiste que le `jti` déjà consommé, pour interdire le rejeu. TTL court → purge
-- opportuniste des lignes anciennes à chaque consommation.
CREATE TABLE IF NOT EXISTS upload_tokens_used (
    jti TEXT PRIMARY KEY,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unipile : mapping per-user du compte LinkedIn connecté sous l'abonnement
-- Unipile (B3). La CLÉ Unipile est partagée (org secret) ; chaque user connecte
-- SON LinkedIn par hosted-auth → un `account_id` distinct sous la même clé. Ce
-- n'est PAS un secret (handle opaque), d'où une table en clair (≠ coffre chiffré).
-- `resolve` : clé partagée + account_id per-user → chacun agit comme lui-même.
CREATE TABLE IF NOT EXISTS unipile_accounts (
    sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    -- canal Unipile (LINKEDIN/WHATSAPP/TELEGRAM/INSTAGRAM/…) : un user a un
    -- account_id DISTINCT par canal, sous la même clé partagée.
    provider TEXT NOT NULL DEFAULT 'LINKEDIN',
    account_id TEXT NOT NULL,
    account_name TEXT,
    -- org de CONTEXTE du binding (scope membre, ADR 0033 B4) : le compte n'est
    -- joignable que depuis cette org. Un même canal peut être connecté dans
    -- N orgs (PK composite).
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    -- le compte consomme un siège de la clé PLATEFORME (comptage/facturation
    -- par org — revendeur/passthrough). FALSE en BYO (l'user paie son instance).
    platform_seat BOOLEAN NOT NULL DEFAULT FALSE,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- SOFT disconnect : la ligne survit (disconnected_at posé) au lieu d'être
    -- supprimée. C'est la PREUVE DE PROPRIÉTÉ durable du compte : une reconnexion
    -- qui RÉUTILISE le même account_id chez Unipile (comportement observé) est
    -- rebindée déterministiquement au même sub — sans elle, l'heuristique du
    -- poll-and-bind (compte créé APRÈS le pending) rate toute réutilisation.
    disconnected_at TIMESTAMPTZ,
    PRIMARY KEY (sub, org_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_unipile_accounts_org ON unipile_accounts(org_id);

-- Corrélation hosted-auth (B3, voie webhook) : le `name` posé sur le lien Unipile
-- ne revient PAS dans /accounts → on pose un **nonce** aléatoire comme `name` et on
-- le mappe au sub. Au succès, Unipile POST {name=nonce, account_id} sur le webhook ;
-- on résout nonce→sub. Le nonce (non devinable, courte vie) sécurise un webhook
-- non authentifié. Consommé à la résolution, pruné après expiration.
CREATE TABLE IF NOT EXISTS unipile_pending (
    nonce TEXT PRIMARY KEY,
    sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    org_id BIGINT,                       -- org de contexte au connect (porté au compte)
    provider TEXT NOT NULL DEFAULT 'LINKEDIN',  -- canal demandé (B1, multi-canal)
    platform_seat BOOLEAN NOT NULL DEFAULT FALSE,  -- siège clé plateforme (ADR 0033 B4)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Grant « opérer mon compte » (otomata-private#55, patron ADR 0025) : le PROPRIÉTAIRE
-- d'un compte Unipile accorde à un membre nommé (d'une org commune) le droit d'opérer
-- son compte sur UN canal. Deny-by-default, révocable, audité (granted_by/granted_at).
-- SEULE exception au no-fallback anti-usurpation (oto-backend#5) — revalidée à CHAQUE
-- appel dans la résolution (révocation = effet immédiat). PK sans account_id : le grant
-- porte sur LE CANAL du owner ; la résolution relit le handle LIVE via JOIN
-- unipile_accounts (owner déconnecté ⇒ grant inerte ; reconnexion ⇒ le grant suit).
-- `account_id` = snapshot du handle AU GRANT (audit/affichage seulement).
CREATE TABLE IF NOT EXISTS connector_account_grants (
    owner_sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    provider TEXT NOT NULL,              -- canal DB (LINKEDIN/WHATSAPP/…)
    account_id TEXT NOT NULL,
    grantee_sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    granted_by TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_sub, provider, grantee_sub)
);
CREATE INDEX IF NOT EXISTS idx_account_grants_grantee
    ON connector_account_grants(grantee_sub, provider);

-- Pointeur « identité opérée » du grantee : (sub, provider) → compte qu'il OPÈRE,
-- DISTINCT de sa ligne de connexion unipile_accounts (qui reste SON compte : org_id
-- de facturation, vue admin seats, disconnect). Posé par select_identity d'un compte
-- accordé, effacé par le retour-à-soi. Jamais un droit : revalidé contre
-- connector_account_grants à chaque appel (backstop dur).
CREATE TABLE IF NOT EXISTS unipile_operated_accounts (
    sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    owner_sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub, provider)
);

-- Palier organization : la table `orgs` est définie en TÊTE du schéma (près de
-- `users`, tables racines) — cf. la note là-bas (#151). Une org possède des
-- credentials propres (coffre `connector_credentials`, entity_type='org') et des
-- opérateurs (org_members) ; source de vérité de l'appartenance = ces tables,
-- résolues par `sub` — JAMAIS un claim du token Logto (le token MCP ne porte que
-- sub). Cf. project_oto_mcp_org_tier.

-- org_role : 'org_admin' | 'org_member' (validé en code, pas par CHECK, comme
-- users.role). is_active = org courante du sub (au plus une TRUE par sub,
-- garantie par l'index partiel + l'écriture ; même pattern que
-- user_google_oauth.is_default).
CREATE TABLE IF NOT EXISTS org_members (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    sub TEXT NOT NULL,
    org_role TEXT NOT NULL DEFAULT 'org_member',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, sub)
);
CREATE INDEX IF NOT EXISTS idx_org_members_sub ON org_members(sub);
CREATE UNIQUE INDEX IF NOT EXISTS org_members_one_active ON org_members(sub) WHERE is_active;

-- Les credentials d'org (Attio, Pennylane, MM token…) vivent dans le coffre
-- chiffré `connector_credentials` (entity_type='org'), pas dans une table dédiée.


-- Invitations (onboarding SaaS). Le token plaintext n'est jamais stocké
-- (seulement son hash, comme user_api_tokens). accepted_at NULL = en attente.
-- **Feature cascade plateforme/org/équipe** (comme les connecteurs) : le SCOPE est
-- dérivé des cibles → `org_id` NULL = invitation plateforme (onboarding pur) ;
-- `org_id` seul = invitation d'org ; `org_id`+`group_id` = invitation d'équipe
-- (colonnes `group_id`/`group_role` ajoutées par ALTER dans _init, après org_groups).
-- `org_id` NULLABLE (plateforme + héritage). `source` = provenance
-- ('org_admin' | 'group_admin' | 'platform_admin').
-- `email` NULLABLE : une invitation nominative cible un email, mais une émission
-- « code à partager soi-même » (sans envoi mail) peut être anonyme. `code` = code
-- court lisible (lien /invitation/<code>), saisi/partagé à la main ; c'est le
-- secret d'accès single-use (≠ token_hash legacy du lien mail).
CREATE TABLE IF NOT EXISTS org_invitations (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
    email TEXT,
    org_role TEXT NOT NULL DEFAULT 'org_member',
    token_hash TEXT NOT NULL UNIQUE,
    code TEXT,
    invited_by TEXT,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    accepted_sub TEXT
);
CREATE INDEX IF NOT EXISTS idx_org_invitations_org ON org_invitations(org_id);
-- idx_org_invitations_code NON déclaré ici : `code` est ajouté par ALTER (DB
-- existantes) APRÈS ce _SCHEMA → l'index sur `code` vit dans le bloc migration,
-- après l'ADD COLUMN (sinon UndefinedColumn au boot sur une table préexistante).

-- Instructions injectées AU NIVEAU PLATEFORME (#50, bloc A « secret sauce » +
-- bloc B « onboarding »). Singleton par `key` ('secret_sauce' | 'onboarding').
-- Éditable seulement par l'admin plateforme (inviolable par l'org — frontière
-- plateforme/org nette). Seedé au boot depuis les constantes de `instructions.py`
-- (INSERT ON CONFLICT DO NOTHING) → le code reste le défaut/fallback, la DB porte
-- l'override éditable. En CLAIR (prose, pas un credential).
CREATE TABLE IF NOT EXISTS platform_instructions (
    key TEXT PRIMARY KEY,                       -- 'secret_sauce' (bloc A) | 'onboarding' (bloc B)
    body_md TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

-- Guides (ADR 0042) — PROSE d'instruction, UNE table pour deux LIVRAISONS :
--   * delivery='on-demand' : how-to chargé à la demande via `oto_guide`
--     (scope org|user en DB ; platform on-demand = fichiers `guides/*.md`, PR) ;
--   * delivery='init' : readme injecté au handshake (bloc A/C) — le MÊME primitif,
--     migré des ex-tables (secret_sauce, *_instructions[claude_md], user_agent_readme).
-- Distincte des PROCÉDURES (`org_instructions`, slots/versioning). CLAIR (pas un credential).
--
-- ⚠️ TABLE EN LECTURE SEULE depuis le lot M1 (blueprint ADR 0063-D4) : ses lignes
-- vivent désormais dans `nodes` (voir juste dessous), plus rien ici ne s'écrit par
-- la façade `db/guides.py`. Elle reste en place — la PROD tourne encore l'ancien
-- code sur CETTE MÊME base, et la conversion la recopie à chaque boot pour
-- rattraper ce qu'elle y écrit. Les deux lecteurs qui vivaient hors façade — la
-- recherche (`db/search.py`) et l'outbox d'embeddings (`db/aux_embed.py`) — sont
-- passés sur `nodes` (#282) : un guide écrit depuis M1 était sorti de `oto_search`
-- sans que rien ne le dise.
-- ⚠️ **Rien ici ne se DROPPE tant que la prod n'a pas été taguée** : ni la table,
-- ni ses colonnes, ni ses index de recherche `idx_guides_fts`/`idx_guides_trgm`
-- (posés par `search.index_ddl`), ni les lignes `aux_embeddings(kind='guide')`.
-- L'ancien code s'en sert en production : les retirer aujourd'hui y casserait la
-- recherche instantanément. C'est le lot d'après (docs/live-migrations.md).
CREATE TABLE IF NOT EXISTS guides (
    id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL,                         -- 'platform' | 'org' | 'group' | 'user'
    owner_id TEXT NOT NULL,                      -- 'platform' | org.id::text | group.id::text | sub
    slug TEXT NOT NULL,                          -- 'readme'/'secret_sauce' (init) | how-to slug
    delivery TEXT NOT NULL DEFAULT 'on-demand',  -- 'init' | 'on-demand'
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope, owner_id, slug)
);

-- NŒUDS (blueprint ADR 0054 + 0063, lot M1) — la table de contenu UNIQUE vers
-- laquelle convergent pages, projets, tableaux, lignes et couches de contexte.
-- Neuve, et pas une extension de `docs` (0063-D1) : la table des pages porte une
-- contrainte contraire au modèle (`project_id NOT NULL`) et un ownership qui vit
-- sur le projet — les traîner dans chaque requête pendant des mois coûterait plus
-- que la conversion.
--
-- ⚠️ CE QUE CE LOT FAIT DISPARAÎTRE, et qu'un lecteur futur ne devinera pas en
-- lisant le code : **le concept de « guide »**. L'ADR 0055-D4 pose qu'une couche
-- de contexte EST une page. Un readme d'org, un how-to plateforme, la note perso
-- d'un utilisateur : ce sont des pages (`kind='page'`), possédées par un scope.
-- Il n'existe donc AUCUN genre « guide » — et l'axe de livraison « injecté /
-- à la demande », qui était la NATURE d'une ligne de `guides` (sa colonne
-- `delivery`, ses deux jeux de fonctions, ses deux surfaces), n'est plus qu'une
-- PROPRIÉTÉ parmi d'autres : `props->>'delivery'`. La table `guides` n'a pas
-- déménagé, elle s'est dissoute. C'est le premier endroit où le modèle unique
-- devient réel — les surfaces (`oto_guide`, `/api/me/guides/*`), elles, ne
-- changent pas d'un octet : elles lisent ici à travers la même façade
-- (`db/guides.py`).
--
-- FORME MESURÉE, pas supposée : c'est la « forme B » du banc d'écriture en masse
-- (`oto-blueprint:docs/banc-ecriture-noeuds.md`), éprouvée jusqu'à un million de
-- lignes. Ne pas l'élargir sans mesure — chaque colonne se paie cent mille fois
-- sur un vivier (0063-D3, garde-fou 1) ; les champs métier vont dans `props`.
CREATE TABLE IF NOT EXISTS nodes (
    id BIGSERIAL PRIMARY KEY,
    -- 0059-D3 : la désignation OPAQUE et immuable, clé des machines (le BIGSERIAL
    -- reste interne). Une table neuve naît avec — d'où la dette du backfill à
    -- double résolution évitée avant d'être contractée.
    -- ⚠️ C'est un IDENTIFIANT, jamais une capacité : le connaître n'ouvre aucun
    -- droit (0055-D9 — les droits viennent des grants, jamais du contenu).
    -- L'unicité est une contrainte d'IDENTITÉ (un identifiant qui collisionne ne
    -- résout plus), pas un index de requête : les index de requête sont les DEUX
    -- ci-dessous, et seulement eux.
    public_id TEXT NOT NULL,
    -- L'arbre. PAS de clé étrangère : « contrainte, ou intégrité portée par le
    -- code ? » est l'arbitrage M-e du chantier, ouvert (le CASCADE hérité de
    -- `docs` rend la suppression d'un tableau ×118 plus chère sans index sur
    -- parent_id). Il se tranche avant M4, pas ici.
    parent_id BIGINT,
    position BIGINT,           -- ordre de la fratrie (entiers espacés, 0063-D2)
    kind TEXT NOT NULL,        -- 'page' aujourd'hui ; tableau | ligne aux lots M3/M4
    owner_type TEXT NOT NULL,  -- platform | tenant | org | group | user (0049/0053)
    -- ⚠️ ÉCART ASSUMÉ avec la forme du banc, qui portait `owner_id BIGINT` : un
    -- propriétaire de scope `user` est un `sub` Logto (`users.sub` est la clé
    -- primaire, il n'existe aucun id numérique d'utilisateur), et le scope
    -- `platform` n'a pas d'id du tout. Un BIGINT obligerait donc à inventer un
    -- surrogate par utilisateur — une migration d'identité, sans rapport avec le
    -- modèle de contenu. TEXT est aussi ce que portent déjà `projects.owner_id`,
    -- `user_datastores.owner_id`, `org_instructions.owner_id` et `guides.owner_id` :
    -- le couple (owner_type, owner_id) se lit de la même façon partout.
    owner_id TEXT NOT NULL,
    props JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Le bail de la file de travail migre TEL QUEL (0063-D3) — colonnes posées
    -- ici, sans lecteur avant la conversion des lignes (M4).
    claimed_by TEXT,
    claimed_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Contrainte NOMMÉE (docs/live-migrations.md) : un futur
    -- `DROP CONSTRAINT IF EXISTS nodes_public_id_key` ne peut pas viser autre chose.
    CONSTRAINT nodes_public_id_key UNIQUE (public_id)
);
-- DEUX index de requête, pas plus (0063-D3 garde-fou 2, confirmé par le banc : le
-- coût du volume se joue là, bien plus que dans la largeur de la ligne) — l'arbre
-- et le propriétaire. Les deux index partiels de M-f (ownership d'une ligne,
-- prédicat du bail) attendent M4.
-- Table et index naissent ensemble ⟹ leur place est ici et pas dans `_init`
-- (cf. le piège « CREATE INDEX d'une NOUVELLE colonne », docs/live-migrations.md).
--
-- ⚠️ Les index de RECHERCHE (GIN d'expression, FTS + trigramme) ne sont pas ici :
-- leur expression DOIT être la même objet que celle de la clause WHERE, donc ils
-- sont construits par `db/search.index_ddl()` (source unique index ↔ requête) et
-- posés par `_init`. Ils portent aujourd'hui sur `nodes` ENTIÈRE, sans prédicat
-- partiel : la table porte des dizaines de lignes. C'est quand les LIGNES de
-- tableau y entreront (M4) que le prédicat partiel achètera quelque chose — sur un
-- vivier, ces GIN pèsent 99 % du temps d'écriture (banc M0). Le décider avant
-- serait le calibrer sur une population qui n'existe pas.
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_owner ON nodes(owner_type, owner_id);

-- Procédures (doctrines/skills) — table UNIQUE, possédée par un SCOPE (chantier
-- procédures, cadrage 10/07) : `owner_type/owner_id` ('org' = procédure d'org,
-- 'group' = procédure d'équipe à la fusion B2 d'org_group_instructions ; `org_id`
-- reste l'org PARENTE dans les deux cas — dénormalisé, FK + prédicats). Chaque
-- procédure est identifiée par `slug` dans son scope ; l'unicité vivante =
-- (owner_type, owner_id, slug) (index unique posé par _init ; la PK legacy
-- (org_id, slug) tombe en B2). En CLAIR (prose, hors coffre). `version` est
-- incrémenté à chaque écriture, qui archive un snapshot dans la table sœur.
-- (Le readme `claude_md` vit dans `guides`, ADR 0042 — plus une ligne d'ici.)
CREATE TABLE IF NOT EXISTS org_instructions (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL DEFAULT 'org',
    owner_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL,
    -- ADR 0035 : slots = entités requises déclarées ({name, type, description?,
    -- connector?}), référencées par nom dans la prose (<slot:name>). Le binding
    -- nom→instance vit dans le projet (project_links), jamais ici.
    slots JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    set_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- PK NOMMÉE : le DROP de la PK legacy (org_id, slug) dans _init cible
    -- `org_instructions_pkey` — un nom distinct protège l'install fraîche.
    CONSTRAINT org_instructions_owner_pkey PRIMARY KEY (owner_type, owner_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_org_instructions_org ON org_instructions(org_id);

-- Historique : un snapshot par version posée (revert + audit). Append-only.
-- Porte le même scope owner que la table vivante (unicité vivante :
-- (owner_type, owner_id, slug, version), index unique posé par _init).
CREATE TABLE IF NOT EXISTS org_instruction_revisions (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL DEFAULT 'org',
    owner_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL,
    slots JSONB NOT NULL DEFAULT '[]'::jsonb,
    set_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT org_instruction_revisions_owner_pkey PRIMARY KEY (owner_type, owner_id, slug, version)
);

-- Bibliothèque PUBLIQUE de doctrines (marketplace de skills/templates). Chaque
-- entrée = une doctrine publiée, avec un AUTEUR : 'otomata' (la plateforme) ou
-- 'org' (un créateur privé = une org). Preview + fork dans son org (copie vers
-- org_instructions sous un nouveau slug). En CLAIR (prose publiable, hors coffre).
-- Table NEUVE → ses index vivent ici (créés atomiquement) ; toute évolution
-- ULTÉRIEURE de colonne/index ira dans le bloc ALTER d'init_db (gotcha ADR 0017).
CREATE TABLE IF NOT EXISTS doctrine_library (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL,
    slots JSONB NOT NULL DEFAULT '[]'::jsonb, -- ADR 0035 : voyage avec la doctrine au publish/fork
    author_kind TEXT NOT NULL,                -- 'otomata' | 'org' (validé en code)
    author_org_id BIGINT REFERENCES orgs(id) ON DELETE SET NULL,
    author_display TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    tags TEXT[] NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL DEFAULT 'public',-- 'public' | 'unlisted' (validé en code)
    source_org_id BIGINT,                     -- org dont la doctrine a été publiée
    source_slug TEXT,
    forked_from BIGINT REFERENCES doctrine_library(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,        -- ré-publication = incrément
    published_by TEXT,                         -- sub du publieur
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (slug)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_library_visibility ON doctrine_library(visibility);
CREATE INDEX IF NOT EXISTS idx_doctrine_library_author ON doctrine_library(author_kind, author_org_id);
CREATE INDEX IF NOT EXISTS idx_doctrine_library_category ON doctrine_library(category);

-- Sous-palier GROUPE (= départements / équipes au sein d'une org, ADR 0012).
-- Une org se subdivise en groupes plats (pas de sous-groupes en v1) ; chaque
-- groupe a un chef d'équipe (group_role='group_admin'). Modèle de droits
-- hiérarchique unifié (platform_admin > org_admin > group_admin > member) :
-- la résolution effective vit dans `roles.py`, l'appartenance dans ces tables.
-- Un groupe GOUVERNE deux ressources, par DÉLÉGATION de l'org : la doctrine
-- (org_group_instructions) et des secrets partagés (coffre
-- `connector_credentials`, entity_type='group'). Source de vérité de
-- l'appartenance = ces tables, résolues par `sub`.
CREATE TABLE IF NOT EXISTS org_groups (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS idx_org_groups_org ON org_groups(org_id);

-- group_role : 'group_admin' (chef d'équipe) | 'group_member' (validé en code,
-- pas par CHECK, comme org_members.org_role). is_active = groupe courant du sub
-- (au plus une TRUE par sub, garantie par l'index partiel — même pattern que
-- org_members.is_active). INVARIANT : le groupe actif appartient toujours à
-- l'org active du sub (posé par set_active_group ; effacé par set_active_org
-- quand l'org bascule).
CREATE TABLE IF NOT EXISTS org_group_members (
    group_id BIGINT NOT NULL REFERENCES org_groups(id) ON DELETE CASCADE,
    sub TEXT NOT NULL,
    group_role TEXT NOT NULL DEFAULT 'group_member',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, sub)
);
CREATE INDEX IF NOT EXISTS idx_org_group_members_sub ON org_group_members(sub);
CREATE UNIQUE INDEX IF NOT EXISTS org_group_members_one_active
    ON org_group_members(sub) WHERE is_active;

-- (Les procédures d'ÉQUIPE vivent dans `org_instructions` avec owner_type='group'
--  depuis la fusion du chantier procédures — cadrage 10/07, Lot B/C. Les tables
--  jumelles org_group_instructions/+revisions sont DROPpées en Lot C.)

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

-- Bascule de tenant Logto (B1, otomata#35) : alias ancien_sub → nouveau_sub. Posé
-- par migrate_sub au 1er login d'un compte sur le nouveau tenant (merge par email).
-- Sert à canonicaliser les tokens encore émis par l'ancien tenant pendant le drain
-- (sinon un vieux token re-créerait le compte supprimé). Vide hors fenêtre de bascule.
CREATE TABLE IF NOT EXISTS sub_aliases (
    old_sub TEXT PRIMARY KEY,
    new_sub TEXT NOT NULL,
    migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- File d'envoi d'email différé (« plus tard » / garde-fou quiet hours). Le HTML est
-- rendu et l'autorisation vérifiée AU MOMENT de email_send (snapshot) ; le worker
-- envoie body_html tel quel, sans re-render ni re-check. scheduled_at en UTC.
CREATE TABLE IF NOT EXISTS scheduled_emails (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
    created_by TEXT,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_html TEXT NOT NULL,
    from_email TEXT,
    from_name TEXT,
    reply_to TEXT,
    transport TEXT NOT NULL,                  -- 'mailer' | 'resend'
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed | cancelled
    scheduled_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_at TIMESTAMPTZ,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_emails(scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_sched_org ON scheduled_emails(org_id, status, created_at DESC);


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
    amount INTEGER NOT NULL,                -- centimes (converti en montant Mollie à la frontière)
    currency TEXT NOT NULL DEFAULT 'eur',
    payment_intent_id TEXT,                 -- tr_xxx (premier paiement, page hébergée)
    payment_id TEXT,                        -- tr_xxx (MIT rejoué / id résolu)
    status TEXT NOT NULL,                   -- statut Mollie observé
    attempt SMALLINT NOT NULL DEFAULT 1,    -- n° de tentative (retries du runner)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_billing_payments_org ON billing_payments(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_payments_open
    ON billing_payments(created_at) WHERE status NOT IN ('paid', 'failed', 'canceled', 'expired');
"""
