---
title: REST API (consommée par oto.ninja /account)
type: reference
description: >-
  Inventaire des endpoints REST /api/* de oto-backend : profil /api/me (billing,
  onboarding, connecteurs), settings LinkedIn/API-keys/tools, doctrine org
  /api/me/instructions*, palier org (CRUD orgs, membres, secrets, invitations,
  entitlements namespace), admin users/grants/tokens/monitoring, billing Stripe,
  bibliothèque publique de doctrines (doctrine_library, visibilité public/unlisted).
  Détaille les règles CORS (oto.ninja, app.oto.ninja, dashboard.oto.ninja), l'autz
  (même JWTVerifier ES384 que /mcp, audience mcp.oto.ninja), et les gotchas secrets
  (jamais la clé en réponse, providers per-user refusés en org secrets). À charger
  pour implémenter ou déboguer un endpoint REST ou comprendre le contrat front/back.
---

# REST API (consommée par oto.ninja /account)

- `GET /api/me` — profil + role + statut LinkedIn + statut providers (mode/key/quota) + `active_org`/`active_org_name`/`org_role` + `avatar_url`/`active_org_logo_url`
- `POST|DELETE /api/me/avatar` — upload (multipart `file`, png/jpeg/webp ≤ 2 Mo) / efface l'avatar user → Scaleway Object Storage, URL publique en DB
- `POST|DELETE /api/orgs/{id}/logo` — upload / efface le logo **uploadé** d'org (org_admin, multipart `file`). Le logo AFFICHÉ (`logo_url` des lectures + `active_org_logo_url` de `/api/me`) est l'**effectif** : upload sinon dérivé du CDN logo.dev via le `domain` déclaré (`org_store.effective_logo_url`, token `LOGODEV_TOKEN`) ; `logo_custom` (fiche org) dit si un upload existe.
- `PATCH /api/orgs/{id}` (+ miroir `/api/admin/orgs/{id}`) — profil d'org (org_admin) : `name`, `description`, **`domain`** (domaine de marque, normalisé `org_store.normalize_domain` — `""` efface, saisie URL tolérée, invalide → 400 `invalid_domain`), `industry`, `location`. Capacité `org.update` (MCP `oto_org(op='update')`, console ADR 0047).
- `POST|DELETE /api/settings/linkedin` — cookie li_at + UA
- `POST|DELETE /api/settings/api-keys/{serper|hunter|sirene}` — user key
- `GET /api/me/tools` + `POST|DELETE /api/me/tools/{name}` — toggle individuel d'un tool MCP
- `GET /api/me/instructions` (index des procédures ; le readme d'org est un guide `delivery=init`, plus servi ici) + `GET|PUT|DELETE /api/me/instructions/{slug}` + `GET /api/me/instructions/{slug}/versions` + `POST /api/me/instructions/{slug}/revert` — procédures de l'**org active** (le slug `claude_md` est RÉSERVÉ au readme et refusé ici) (cf. §Doctrines). Lecture = membre ; écriture = `org_admin` (ou platform admin). Édité par le dashboard (`/procedures`).
- `GET|PUT|DELETE /api/me/guides/{scope}/{slug}` (+ `GET /api/me/guides`) — **la prose d'instruction**, un seul primitif sur deux axes (ADR 0042 §Convergence des surfaces) : `scope` ∈ platform|org|group|user × `delivery` ∈ `on-demand` (défaut, un how-to chargé au besoin) | `init` (**readme injecté à chaque session**, slug canonique `readme` — corps vide = couche effacée). Miroir REST d'`oto_guide`, mêmes handlers. Écriture gatée par scope (platform_admin / org_admin / chef d'équipe / self). Variantes par-id pour viser une org/équipe précise plutôt que l'active : `GET|PUT /api/orgs/{id}/guides/{scope}/{slug}` et `/api/groups/{id}/guides/{scope}/{slug}`. *(Remplace `GET|PUT /api/me/agent-readme`, retiré le 2026-07-28.)*
- `POST|DELETE /api/me/projects/{id}/public-share` — **partage public CHIFFRÉ** d'un projet (ADR 0032 §3, zero-knowledge). Le dashboard chiffre le snapshot (brief + pages) côté navigateur et POSTe uniquement `{ciphertext}` ; renvoie `{token, public_base_url}`. Écriture = `ownership.can_access(project, write)`. La clé de déchiffrement n'atteint JAMAIS le serveur (fragment d'URL).
- `GET /api/public/projects/{token}` — **sans auth** : renvoie `{ciphertext, updated_at}` du snapshot chiffré. Déchiffrement côté navigateur (route `/p/p/{token}#<clé>`). Pendant public de `GET /api/public/docs/{token}` (#4a).
- `PUT|POST|GET /api/upload/{token}` — **réception d'un upload signé out-of-bande** (issue #105), **pas de JWT** : le `{token}` est un jeton HMAC scellant `(sub, org, cible)` + TTL court + usage unique (émis par `oto_upload_url`, module `upload_tokens.py`). **PUT** = un agent avec shell y pousse le corps brut (`curl --data-binary @fichier`) ; **POST** multipart `file` = le formulaire humain ; **GET** = page HTML d'upload autoportée (fallback quand l'agent n'a pas de shell, ex. claude.ai : il transmet le lien à l'humain — le jeton n'est PAS consommé au GET). Le backend matérialise dans la cible en **réappliquant** son autz, consomme le jeton (anti-rejeu), renvoie un **accusé léger** (id + compteurs), jamais le body. Cibles : page Documents (`doc`), fichier brut de projet (`project_file`, autz `ownership.can_access(project, write)`), lot de lignes datastore (`datastore` — NDJSON/CSV batch-upsert sur clé, autz `ownership.can_access(datastore_namespace, write)`, ns_id scellé au mint). Évite de faire transiter du gros contenu par le contexte du LLM.
- `GET /api/admin/users` + `POST /api/admin/users/{sub}/role` — admin only
- `POST /api/admin/users/{sub}/grants/{key_id}` body `{daily_quota}` — set/update quota par grant (admin only)
- `GET|POST /api/admin/users/{sub}/tokens` + `DELETE /api/admin/users/{sub}/tokens/{token_id}` — issue/list/revoke tokens API on behalf of a user (admin only)
- `GET /api/admin/monitoring/summary?days=` + `GET /api/admin/monitoring/calls?limit=&sub=&tool=&errors=&days=` — journal des appels MCP, agrégats + brut (admin only, cf. §Monitoring)
- `GET /api/orgs/{id}/monitoring/{summary,calls,calls/{call_id},connectors,adoption,runs,runs/{run_id},gaps,tool-quality}` — **les mêmes lentilles au niveau ORG** (`capabilities/org_monitoring.py`, autz `ORG_ADMIN_OF`, face MCP `oto_org_monitoring`). Scope = `tool_calls.org_id`/`usage_signals.org_id` (ce qui a été émis SOUS l'org), jamais l'appartenance des membres. `adoption` n'existe qu'à cet étage (membre par membre : actif / jamais actif / bloqué par un connecteur). ⚠️ `calls/{call_id}` et `runs/{run_id}` rendent **404** hors de l'org (id séquentiel devinable). Sert la page dashboard `/org/monitoring`. Cf. `docs/monitoring.md` §Trois étages.
- **Palier org** (`api_routes_orgs.py`, projection 1:1 des meta-tools `oto_admin_*org*` / `oto_list_orgs`) :
  - self-service : `GET|POST /api/me/orgs` (**`POST` = `org.create` self-serve**, créateur→org_admin, cap `OTO_MCP_MAX_ORGS_PER_USER`) ; `GET /api/orgs/{id}` ; `POST|DELETE /api/orgs/{id}/members[/{sub}]` + `PUT|DELETE /api/orgs/{id}/secrets/{provider}` (org_admin)
  - **invitations — feature cascade plateforme/org/équipe** (le scope est DÉRIVÉ des cibles : org_id NULL = plateforme, org_id seul = org, org_id+group_id = équipe). Trois faces émettrices, une seule acceptation :
    - **org** : `POST|GET /api/orgs/{id}/invitations` + `DELETE …/{inv}` (org_admin ; `oto_org` op=invite).
    - **équipe** : `POST|GET /api/groups/{id}/invitations` + `DELETE …/{inv}` (group_admin ; `oto_group` op=invite). L'invité rejoint l'org parente PUIS l'équipe à l'acceptation.
    - **plateforme** : `POST|GET /api/admin/invitations` + `DELETE …/{inv}` (platform_admin ; `oto_admin_invite` op=create/list/revoke). `org_id` optionnel (vide = onboarding pur, sinon rattachement direct).
    - **acceptation commune** : `POST /api/me/invitations/accept` (`SUB_ONLY`, token/code, match email vérifié + expiry). Email via `oto_mcp/email.py` (otomata-mailer `mailer.oto.zone/api/send`, env `OTO_MAILER_SEND_BEARER`, best-effort → `invite_url` en repli ; **plus de Resend**).
  - **fiche admin user** : `GET /api/admin/users/{sub}` = identité + accès effectif par provider (`status_for`) + grants + namespaces + orgs (membership).
  - platform admin : `GET|POST /api/admin/orgs`, `GET /api/admin/orgs/{id}` (+ entitlements), `…/members*`, `…/secrets/{provider}`, `POST|DELETE /api/admin/orgs/{id}/entitlements/{namespace}`, `GET /api/admin/namespace-grants`, `POST|DELETE /api/admin/users/{sub}/namespace-grants/{namespace}`
  - secrets : jamais la clé en réponse (provider/base_url/set_at/set_by) ; providers per-user (slack/linkedin/google/whatsapp) refusés en `400` ; listing lu du coffre canonique `credentials_store` (legacy `org_secrets` plus dual-written sous chiffrement). Gating org_admin/membre via `org_store.get_org_role` (platform admin toujours autorisé). Révocation lazy sur sessions MCP ouvertes. Contrat front : `oto-app/docs/ORG_API_CONTRACT.md`.
- **Bibliothèque publique de doctrines** (marketplace de skills, table `doctrine_library`) :
  capacités `library.*` (`capabilities/doctrine_library.py`, montage auto MCP+REST) —
  `library.list/get` (`SUB_ONLY`, MCP `oto_procedure` op=library_list/library_get + REST
  `GET /api/me/doctrines/library[/{slug}]`), `library.publish`/`library.fork` (`ORG_MEMBER` +
  gate org_admin en handler, MCP `oto_procedure` op=publish/fork + REST
  `POST /api/me/doctrines/{publish,fork}`), `library.unpublish` (auteur/PLATFORM_ADMIN, `DELETE
  /api/me/doctrines/library/{id}`). **Auteur** = `otomata` si publieur platform-operator, sinon
  l'`org`. **Fork** réutilise `org_store.set_instruction` → skill d'org versionné. Surface
  ANONYME pour la vitrine : routes écrites à la main `GET /api/doctrines/library[/{slug}]`
  (deny-by-default `visibility='public'`, l'adaptateur capacité authentifie toujours).
  **`visibility`** : `public` (dans le catalogue) vs `unlisted` = **lien non listé** (style
  YouTube) — servie par `library.get` (slug exact, tout user authentifié) mais **jamais**
  listée (`list` force `include_unlisted=False`) ni servie en anonyme. Partage par lien, pas
  un secret d'org : une doctrine sensible ne se publie pas (reste un skill d'org privé).
- CORS : `oto.ninja`, `app.oto.ninja`, `dashboard.oto.ninja` (+ localhosts dev) — défaut dans `_allowed_origins`, override `OTO_MCP_CORS_ORIGINS`. `account.oto.zone` retiré (surface compte décommissionnée → dashboard.oto.ninja)
- Même `JWTVerifier` que `/mcp` — partage l'audience `https://mcp.oto.ninja/mcp`

## Descriptif OpenAPI — `GET /openapi.json` (aussi `/api/openapi.json`)

**Sans auth**, comme `/api/mcp/catalog` : un descriptif décrit des FORMES, aucune valeur.
**Dérivé à chaque requête** (`openapi.py`) de deux sources — le registre de capacités
(chemin + verbe + description + JSON Schema de l'`Input` pydantic) et la **table de routes
vivante** de l'app pour les routes encore écrites à la main (chemin + méthodes, sans schéma,
taggées `_legacy`). Rien n'est saisi à la main, donc rien ne peut mentir. `/api/admin/*` en
est retiré (console de la plateforme, pas d'intégrateur tiers).

⚠️ **À lire avant de conclure qu'une surface manque.** La consolidation ADR 0047 a déplacé le
verbe dans le CORPS : `POST /api/me/projects {"op":"list"}` n'est pas « créer un projet »,
c'est **toute** la surface projet (list/get/runs/inventory/link/publish_mcp…). Un intégrateur
qui sonde `/api/projects` obtient 404 et conclut « les projets ne sont pas sur REST » — c'est
arrivé (brief scout, 08/2026). Le descriptif rend l'énuméré `op` lisible, ce que le sondage de
chemins ne donnera jamais. Même forme pour `/api/me/docs`, `/api/me/kb`, `/api/resources`.

## Jetons API `oto_` — gestion et portée

- **La gestion des jetons demande une session interactive.** `GET|POST /api/me/tokens`,
  `DELETE /api/me/tokens/{id}` et leurs miroirs admin `/api/admin/users/{sub}/tokens*`
  refusent un porteur de jeton (`403 api_token_forbidden`) : seul un JWT Logto y passe.
  Sinon une fuite est **auto-entretenue** — l'attaquant s'émet un second jeton (non-expirant)
  avant qu'on révoque le premier, et peut révoquer les jetons légitimes. Émettre un jeton
  redevient un acte humain, ce qui borne la gravité réelle d'une fuite à la portée du jeton.
- **Portée opt-in** (`token_scopes.py`, colonne `user_api_tokens.scopes` JSONB) : à la
  création, `POST /api/me/tokens {"label":"scout", "scopes":{"namespaces":{"leads":"read"}}}`
  rend un jeton **porté** — deny-by-default, il n'ouvre QUE les tableaux nommés, en `read`
  ou `write` (write ⊃ read), et **rien d'autre** : ni `/api/me`, ni les connecteurs, ni les
  projets, ni la gouvernance du tableau (créer/supprimer/renommer/partager). Hors portée →
  `403 token_scope_forbidden`. C'est la forme à confier à une intégration tierce ; sans elle,
  un jeton **est** le sub et ouvre toute l'organisation.
  - `scopes` absent ⇒ jeton NON porté = comportement historique. Aucun jeton existant n'est
    touché, aucune migration.
  - Seule réponse **filtrée** plutôt que refusée : `GET /api/datastore/namespaces` rend les
    tableaux de la portée, droits **rabattus** sur ceux du jeton (`permission`/`can_write`/
    `can_govern`) — sans lui une intégration n'aurait pas le schéma de son tableau
    (`page_rows` ne le rend pas) et ne pourrait pas peindre ses colonnes.
  - La table des routes autorisées (`token_scopes._ALLOWED`) est la **seule** porte : une
    route ajoutée demain est refusée sans qu'on ait à y penser.
  - ⚠️ La portée nomme le tableau par son **nom** (ce que l'URL adresse), pas par son id :
    après un renommage, ré-émettre le jeton.
