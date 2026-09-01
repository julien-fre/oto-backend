# oto-mcp

MCP server (Streamable HTTP) exposant les connecteurs **oto-core** (`oto.tools`, importés directement
— **plus aucune dép à la CLI**) comme tools. **Prod** = `https://mcp.oto.cx/mcp` (box Scaleway dédiée),
`mcp.oto.ninja` = **preprod** (ADR 0040). **oto-mcp = le produit central, déployable** : SaaS OU
on-premise (`Dockerfile`, config 100 % par env) ; oto-cli = façade locale basse priorité, tout open
source. Gestion utilisateur = oto.ninja `/account`, via REST.

> **Ce fichier est une CARTE, pas un journal** : conventions, garde-fous, pointeurs ; le détail vit
> dans `docs/`, index en bas. **Un lot qui change un concept met à jour le doc du concept dans le même
> commit**, pas la carte.

## Stack

Python 3.10 (`>=3.10`, ce qu'a tuls.me) · `fastmcp>=3.4.2` (plancher = dernier) + SDK `mcp` ·
`psycopg[binary]` + `psycopg-pool` (PG managé Scaleway `otomata-main`, DB `oto_mcp`) · JWT Logto ES384.
⚠️ **`oto-core[anonymize]` est PINNÉ sur un tag git** ; `pip` **ne réinstalle pas** une dép VCS déjà là,
et **le pin est édité par TOUTES les sessions //** → bumper en **superset**, garder la haute.
⚠️ **Une grappe de rouges en local sur les connecteurs les plus RÉCENTS, avec
`No module named 'oto.tools.<connecteur>'` au fond, n'est PAS ton lot** : c'est le venv partagé en
retard sur le pin — la CI, qui installe au tag, passe. Reconnaître et rejouer sur pristine sans
muter le venv partagé : **`commands.md` §Pin oto-core → « Faux rouge »**.
⚠️ **Les rows PG sont des DICTS** — `r["col"]`, jamais `r[0]`.

## Architecture

⚠️ **Le dossier d'un fichier EST son domaine** : ≥ 4 fichiers au même marqueur → package, `tests/` en
miroir, **jamais** de ré-export à l'ancien chemin (`conventions.md` §Où vit un fichier).

```
oto_mcp/
├── server.py       # FastMCP + uvicorn, montage des routes /api et des tools
├── capabilities/   # les CAPACITÉS (ADR 0009), rangées par domaine
├── api/            # face REST : la TABLE de routes (ordre = contrat) + 1 handler/domaine
├── auth/           # qui parle, et comment un credential s'acquiert
├── connectors/     # la GOUVERNANCE : activation, sélection, identités, link, verify
├── providers/      # le REGISTRE, 1 déclaration/connecteur — doit rester PUR
├── tools/          # les outils servis à l'agent, 1 module/connecteur
├── fod/            # clients FOD (ADR 0028), données publiques FR
├── datastore/      # le spine de records typés
├── middleware/     # la chaîne MCP — l'ORDRE d'enregistrement est un contrat
├── access/         # rôles, contexte, cascade, quotas — plate access.<fn>
├── org_store/      # le palier ORG (orgs, members, vault, settings, library)
├── db/             # le store PG — surface plate db.<fn>
└── config.py       # require_env
deploy/             # systemd (/opt/oto-mcp, 9103), Caddyfile, DEPLOY.md
```

**4 couches à frontière à sens unique** (ADR 0004) : **backend-core** (`db`, `credentials_store`,
`org_store`, `access`, `crypto`, `providers`, `auth.hooks`) — **adaptateur MCP** — **adaptateur REST**
— **runtime connecteurs** ; les trois derniers dépendent du core, **jamais l'inverse**, et l'appellent
**par interface**. Une opération sur **deux faces** s'écrit UNE fois : une **capacité** (ADR 0009) =
handler + `Input` pydantic + `authz` + bindings. ⚠️ **Une route neuve naît CAPACITÉ**, pas dans
`api/` ; **secret brut jamais en argument MCP** ; la table de routes est **FIGÉE**.
**`docs/architecture.md`, `docs/couches-et-capacites.md`.**

## Auth Logto & tenants

JWT Logto **ES384** (le défaut RS256 rejette tout), discovery RFC 9728, façade DCR pour les clients
sans DCR ; au-dessus des orgs, l'étage **tenant** (ADR 0052) — un partenaire sert oto sous sa marque.
⚠️ **Logto = 2 instances** : la prod/preprod est **`auth.oto.ninja`**, PAS `.zone`. ⚠️ **Un env-liste
s'ÉTEND, ne se remplace jamais** (`MCP_AUDIENCE_ALT`, `OTO_MCP_CORS_ORIGINS`, `MAILER_FROM_DOMAINS`,
SPF, redirect URIs) et chaque env a SA liste. **`docs/auth-logto.md`, `docs/tenants.md`.**

## Rôles, coffre & résolution de clé

3 paliers `member < admin < super_admin` ; résolution par appel : `clé membre (sub, org) >
group_secret > org_secret > clé de TENANT > platform_grant` ; tout connecteur dont le credential se
**POSE** est **multi-compte**. Chemin chaud de tout appel. ⚠️ La cascade = **walker unique
`access.walk_cascade`**, jamais recopiée dans un call-site ; compte nommé introuvable partout ⇒
« introuvable », **jamais un repli plateforme silencieux**. **`docs/roles-and-resolution.md`,
`docs/connector-vault.md`.**

## REST API

Endpoints `/api/*`, même `JWTVerifier` que `/mcp` ; `GET /openapi.json` **dérivé** du registre de
capacités ; un jeton `oto_` peut naître **porté**, sa gestion exigeant une session interactive.
⚠️ **CORS : la liste du code est MORTE** — les deux box posent `OTO_MCP_CORS_ORIGINS` dans leur `.env`
(une origine de plus = éditer l'env des **deux** + restart). **`docs/rest-api.md`.**

## Autres sous-systèmes

**Browser & cookie-bound** (ADR 0026) : plus AUCUN browser sur la box — l'API privée cookie-bound
passe par **Browserbase**, LinkedIn par Unipile, le générique `browser` traitant un site comme un
compte du coffre (`docs/browser-automation.md`).
**Messagerie** : `unipile` = le **compte**, plus six **connexions** au nom du réseau ; les noms de
tools ne bougent pas. ⚠️ **`namespace_of` résout au plus long préfixe DÉCLARÉ**, plus au 1er token —
sinon `linkedin_unipile_*` et `linkedin_aiark_*` tomberaient sous un même gate (`docs/unipile.md`).
**Email per-org** : `scaleway` (TEM) et `resend` en BYO-org, `email_send` routant
`sender→connecteur→transport`. ⚠️ Le front qui héberge une org est **dérivé de l'org CIBLE**.
⚠️ **Les 6 gabarits transactionnels servent `users.locale` du DESTINATAIRE** (`'en'`, FR par défaut,
01/09/2026) — texte extrait dans `email_templates.py` pour tenir sous 500 lignes (`docs/email.md`).
**Recherche & KB** : `oto_search` = LE verbe « retrouver », fusion RRF lexicale + sémantique.
⚠️ **Invariant « cherchable ⇔ lisible »**, tripwire par source = critère de merge
(`docs/search-and-kb.md`).
**Onboarding & profil** (ADR 0032 §7) : plus de mode d'accueil, c'est **un projet « Découverte »**
semé à la création de l'org perso ; `oto_whoami` avant une action sensible
(`docs/onboarding-et-profil.md`).
**Runner** : l'**ÉTAT** ici (`run_messages`, `runner_jobs`, `runner_triggers`), la **BOUCLE** dans
`otomata-tech/oto-runner`. ⚠️ La reprise inter-agents lit le **JOURNAL**, jamais le fil
(`docs/runner-et-automatisations.md`).
**Fédération, MCP Apps, veille** : **mount** (OAuth per-user) vs **remote** (bridge M2M d'org), aucun
mount monté d'office ; `prefab_ui` sert une UI rendue en `*_app` (⚠️ **pas d'annotation
`-> Card`**, NameError au boot) ; ⚠️ **guides = tout-DB**, `oto_mcp/guides/*.md` ne sont que des seeds.
**`docs/federation.md`, `docs/mcp-apps.md`, `docs/mcp-spec-watch.md`.**

## SIRENE stock (DuckDB/parquet INSEE)

Stock complet interrogé par DuckDB depuis l'Object Storage (ADR 0002) ; tools `fr_stock_*` + REST
`/api/sirene/*` (**noms de routes inchangés**, `oto-cli`/`oto-core` en dépendent). ⚠️ Pour **chercher**
des boîtes, préférer **`fr_search`** (indexé, <1 s) ; le parquet = lookups, bulk, énumération.
⚠️ **`categorie_entreprise` est calculée sur le GROUPE, pas sur l'entité** — `fr_groupe` sépare les
deux. **`docs/sirene-stock.md`.**

## Datastore (ADR 0016)

Stockage structuré PG/JSONB natif : tools `data_*` + REST `/api/datastore/*` (**100 % dérivée**), le
code découpé par **coutures** (`db/datastore_ns` = le TABLEAU, `db/datastore` = les LIGNES,
`datastore/core` = le store qui COMPOSE). ⚠️ Oto gère les **types standards**, jamais l'interprétation
métier d'une VALEUR. ⚠️ **Une pose de schéma REMPLACE** — pour ÉDITER, `data_patch_schema` fusionne
par clé. **`docs/datastore.md`.**

## Ressource possédée & projets (ADR 0030/0032)

`ownership.py` = seam unique : `(owner_type∈{user,group,org}, owner_id)` + `resource_grants`
deny-by-default ; **deux plans jamais confondus**, `can_access` (contenu) vs `can_govern`
(gouvernance). Le **projet** est le conteneur de travail possédé (`oto_project`/`oto_doc`, partagé par
`oto_resource`). ⚠️ **Une LISTE de contenu scope sur `active_owner(current_org)`, JAMAIS
`owner_pairs()`** (fuite fail-open). **`docs/ownership.md`, `docs/projects.md`.**

## Journal des appels, Sentry & usage (ADR 0017)

`ToolCallLogger` journalise chaque appel dans `tool_calls` (identité = `sub` du JWT), lu par trois
étages de lentilles (membre / org / plateforme), avec le feedback d'agent et les déroulés ; les
exceptions partent vers **Sentry** (EU). ⚠️ **Ne trace pas la connexion d'un connecteur ni le
`tools/list`** → **compte actif ≠ usage**. ⚠️ **Jamais un jeton en clair**, et **ce n'est PAS une purge
de logs** : la table est la **source de vérité des exécutions**. **`docs/monitoring.md`.**

## Visibilité des outils (ADR 0019/0050)

Denylist calculée `(sub, org active)` dans `session_visibility.py`, appliquée au handshake ; régime
**NOMINAL « non-sélectionné = masqué »**. ⚠️ **`PROTECTED_TOOLS` = quatre familles jamais masquables ni
désactivables** (méta-toolset + identité, échappatoires de contexte, boucle d'usage, dispatch
universel). ⚠️ **Gouvernance, PAS une barrière de sécurité** (ADR 0031), additive : une équipe ne
RÉVÈLE jamais ce que l'org a masqué. ⚠️ **Stdio local = accès complet.** **`docs/tool-visibility.md`.**

## Org/équipe : session, maison, consultation (ADR 0023)

Le pointeur « org active » est scindé en **3 notions** — session (MCP) / consultation (REST,
`X-Oto-Org`) / maison — résolues par le **seam unique** `access.current_org(sub)` = `jeton d'appel ??
org du run ?? consultation ?? maison` ; **TOUTE résolution d'action passe par ce seam**, qui est
**scopé sur l'ACTEUR courant** — jamais pour l'état d'un **tiers** (org/groupe par kwarg).
**`docs/org-context.md`.**

## Guides, agent readme & procédures

**Agent readme** = prose libre **injectée à chaque session**, cumulée du général au spécifique
(plateforme → org → équipe active → user) ; les 4 étages vivent dans `guides` delivery='init' et
s'éditent par **UNE** surface : `me.guide{,s}` (ADR 0042) ; **procédure** = guide nommé, chargé à la
demande. ⚠️ **Le produit dit « guide » depuis le 28/08/2026** (#519), l'ancien nom restant servi avec
**une date de retrait écrite** (`docs/alias-deprecies.md`). ⚠️ Une procédure s'**OUVRE sur son digest**
(jamais fabriqué — sourcé sur le journal des runs, ou rien) et **embarque son SCHÉMA** : deux sections
requises. ⚠️ **La grammaire du dessin est un CONTRAT** (reparsé en graphe) : **UN** seul bloc fencé
**non tagué** — guide plateforme `procedure-flowchart`. **`docs/guides.md`.**

## Groupes & hiérarchie de droits (ADR 0012)

Une org se subdivise en **groupes** avec un **chef d'équipe** (`group_role='group_admin'`) ; droits
**centralisés dans `roles.py`** : `platform_admin ⊇ org_admin ⊇ group_admin ⊇ member` ; un groupe
gouverne par délégation les secrets partagés, les **procédures** et la gouvernance de connecteur.
⚠️ Les procédures d'équipe vivent dans `org_instructions` (`owner_type='group'`) et passent par le
store UNIFIÉ `org_store.<fn>('group', id, …)` (#681, 31/08/2026) ; **leur écriture est gardée
« membre » de l'équipe — la suppression reste au chef** : *la garde suit le VERBE*, écrire est
réversible, supprimer emporte l'historique. ⚠️ **Invariant monotone** : l'équipe RÉTRÉCIT ce que l'org
expose, jamais l'inverse. ⚠️ **Groupe actif** : ≤ 1 par sub, il appartient à l'org active.
⚠️ **Aucun module d'`org_store/` n'importe `group_store`** — vérifié
(`test_org_store_surface_frozen.py`). **`docs/groups-and-roles.md`, `docs/live-migrations.md`.**

## Conventions

**`docs/conventions.md` — à lire avant d'écrire du code ici** : test qui décrit le système et non
l'intention, garde-fou exercé sur le montage RÉEL, aucune adresse en dur, jetons de contexte réservés,
budget d'un retour d'outil, **où vit un fichier**, ordre des middlewares, MONO-LOOP, cycle d'un
connecteur. ⚠️ **Le refus est bruyant, la divergence est muette — et le CI le vérifie** :
`lint_silences.py` refuse un `except Exception` qui ne re-lève, ne journalise ni ne rend un refus
nommé ; échappatoire unique `# noqa: SILENT — <raison>`. **`docs/silences-2026-08-27.md`.**

## Le démarrage (ADR 0065)

**Au boot, le DDL additif et rien d'autre** : `_prepare_database()` **une seule fois par process**.
⚠️ **La fenêtre du healthcheck est FINIE : 120 s** — un travail one-shot ajouté au boot se mesure
**avant** de poser son tag. **Ce qui n'a rien à faire au boot va en maintenance** (`oto-mcp
maintenance …`, timer quotidien **prod seulement**) : *un coût qui suit la taille de la base n'est pas
une migration, c'est un cron.* **`docs/migrations-versionnees.md` §1.**

## Infra

**Box Scaleway dédiée** (ADR 0002) : oto-backend isolé + Caddy ; **DB** = PG managé partagé
(`otomata-main`, DB `oto_mcp`) ; coffre `connector_credentials` chiffré au repos (AES-256-GCM, master
key en Secret Manager au boot, 0 plaintext) ; S3 pour avatars/logos.

> **Détails machine = repo privé `otomata-tech/infra`** (IPs, IDs de secrets/zone/instance, systemd,
> runbook, env) — pas ici, ce repo est public : `infra/docs/oto-platform-state.md` + les docs ciblés
> (`scaleway-managed-db.md`, `caddy.md`, `cloudflare.md`, `deploy-keys.md`) ; intervention prod =
> skill `prod-init`. ⚠️ **PROD et
> PREPROD partagent la MÊME base** : ce qu'on écrit depuis la preprod est **la donnée de prod**, et une
> config portée par une COLONNE n'a qu'**une** valeur pour les deux. `docs/live-migrations.md`.

## Docs

- `conventions.md` — règles de travail, où vit un fichier · **à lire en premier**
- `connector-model.md` — un connecteur : disponibilité / auth / option · **puis**
- `commands.md` — tests, deploy, logs, inspection DB, pin oto-core, et les pièges qui coûtent une
  heure (venv sans pytest, clone qui teste en réalité le tree partagé, **faux rouges d'un venv en
  retard sur le pin**, registre d'outils vide)
- `architecture.md` — l'arbre des modules, les 4 couches
- `couches-et-capacites.md` — ADR 0004 + capacités
- `connector-vault.md` — registre, coffre, instances
- `roles-and-resolution.md` — paliers, cascade de clé
- `groups-and-roles.md` — hiérarchie de droits
- `org-context.md` — session / maison / consultation
- `ownership.md` — `can_access`/`can_govern`, partages
- `tool-visibility.md` — denylist, `PROTECTED_TOOLS`
- `auth-logto.md` — Logto, DCR, jetons `oto_`
- `tenants.md` — l'identité au-dessus des orgs
- `rest-api.md` — endpoints, OpenAPI, jetons, CORS
- `noeuds.md` — le NOUVEL univers de contenu : page/tableau/ligne, `props` vs `data`, les deux
  univers côte à côte, l'arrêt de la recopie
- `datastore.md` — spine PG `data_*`, OAuth Google
- `datastore-colonne-tableau.md` — sa spec
- `projects.md` — liens, partage, périmètre d'URL
- `search-and-kb.md` — `oto_search`, RRF, grains
- `guides.md` — guides & skills d'org, procédure
- `alias-deprecies.md` — noms doublés, date de retrait
- `onboarding-et-profil.md` — Découverte, `me.profile`
- `unipile.md` — split compte/canaux, DSN, identités
- `browser-automation.md` — Browserbase, cookie-bound
- `email.md` — envoi per-org, quiet hours
- `federation.md` — mount vs remote/bridge
- `mcp-apps.md` — `prefab_ui`, convention `*_app`
- `mcp-spec-watch.md` — les SEP, pas les specs
- `runner-et-automatisations.md` — l'état ici, la boucle ailleurs
- `usage-loop.md` — calllog, feedback, déroulés
- `monitoring.md` — enquête, rétention, Sentry
- `event-loop-perf.md` — les 3 gels mono-loop
- `silences-2026-08-27.md` — `except` muets, `# noqa: SILENT`
- `redaction.md` — rédaction de champs, résultat servi
- `live-migrations.md` — migrations vivantes, base partagée
- `migrations-versionnees.md` — ce que le boot exécute
- `sirene-stock.md` — DuckDB sur parquet INSEE
- `connector-test-gate-theirstack-origami.md` — porte de test locale
- `billing.md` — abonnement par org, Mollie, TVA
