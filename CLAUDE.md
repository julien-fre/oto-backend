# oto-mcp

MCP server (Streamable HTTP) qui expose les connecteurs **oto-core** (`oto.tools`,
importés directement — **plus aucune dép à la CLI**) comme tools, branchable dans
claude.ai et Claude Code. Public **prod** = `https://mcp.oto.cx/mcp` (box Scaleway
dédiée ; `mcp.oto.ninja` = **preprod** depuis le cutover ADR 0040 — cf. §Auth « CUTOVER »).

**Positionnement : oto-mcp = le produit central, déployable** (SaaS hébergé OU
on-premise pour un client — image `Dockerfile`, config 100% par env). oto-cli =
façade locale basse priorité (fallback LinkedIn browser). Tout open source.

La page de gestion utilisateur (cookie LinkedIn, etc.) vit dans le site Vue
oto.ninja sous `/account` et parle au MCP via REST.

## Stack

- Python 3.10 (target `>=3.10` — c'est ce que tuls.me a)
- `fastmcp>=3.4.2` (plancher = dernier ; prod aligné au deploy via `pip install -e .`) + `mcp` SDK
- **`oto-core[browser]` PINNÉ sur un tag git** (`@ git+…@vX.Y.Z` dans `pyproject.toml`, plus `@main` flottant ni dép `oto-cli`) : une version déployée = coordonnée reproductible. ⚠️ **`pip` ne réinstalle PAS une dép VCS déjà présente** (`oto-core` "satisfait" quelle que soit sa version) → `pip install -e .` seul ne monte JAMAIS oto-core au tag bumpé. Le deploy **force-réinstalle** oto-core depuis le tag lu du `pyproject` (`pip install --force-reinstall …@$tag`). Bump connecteurs = tag oto-core + édit du pin + deploy (PAS de `git pull` box). Cf. ADR 0020. ⚠️ **Symptôme trompeur en LOCAL** : des tests rouges peuvent être un venv en retard sur le pin, pas du code cassé (05/08 : 17 tests d'un connecteur neuf échouaient, son module n'existant pas dans l'oto-core installé). Réaligner avant de conclure — `uv pip install --python .venv/bin/python --force-reinstall --no-deps "oto-core[…] @ git+…@<tag du pyproject>"`. (⚠️ box `otomata-0` a un VIEUX oto-mcp décommissionné/stoppé avec un editable legacy `oto-cli` pré-split — ne pas s'y fier, le runtime live est la box dédiée.) ⚠️ **Le pin est un champ que TOUTES les sessions // éditent → régressions silencieuses récurrentes** : vécu 2026-07-07, un commit concurrent a réécrit le pin `v1.18.0→v1.17.0` et **cassé un tool déployé SANS erreur** (le tool était enregistré, sa méthode absente de l'ancien oto-core → `AttributeError` seulement à l'appel). Toujours bumper en **superset** (tag haut ⊇ tags bas) ; à la moindre divergence de pin en merge/rebase, **garder la version haute**.
- `psycopg[binary]` + `psycopg-pool` (PostgreSQL managed Scaleway `otomata-main`, DB `oto_mcp`) pour le state par utilisateur — migré depuis SQLite le 2026-05-20. Row factory custom dans `db/_conn.py` (`_str_dict_row`) qui normalise `datetime`/`date` → strings "YYYY-MM-DD HH:MM:SS" : sinon `JSONResponse` crash sur `/api/me` car le code historique attend des strings comme avec SQLite. ⚠️ **Les rows sont des DICTS (accès par nom de colonne `r["col"]`), JAMAIS positionnel `r[0]`** (→ `KeyError: 0`). Vécu 2026-06-25 : deux fonctions RBAC en `r[0]` plantaient à chaque appel, **masqué** par leur fail-open + des tests qui stubbaient ces fonctions → bug invisible jusqu'à un seed réel. Leçon : un **fail-open silencieux + des tests stubbés cachent un bug de forme de row** ; exercer le vrai chemin (cf. [[feedback_verify_empirically]]).
- Auth = JWT Logto (`RemoteAuthProvider + JWTVerifier(jwks_uri=…, algorithm="ES384")`)

## Architecture

```
oto_mcp/
├── server.py         # FastMCP + uvicorn, _SERVER_INSTRUCTIONS, routes /api, tools
├── tools/            # 1 module par connecteur, chacun expose register(mcp)
├── api_routes.py     # /api/me, /api/settings/*, /api/admin/* (CORS oto.ninja)
├── access.py         # rôles member/admin, resolve_api_key, quotas, status_for
├── db/               # store PG (package) : _conn (pool/connexion), _schema (DDL), _init (migrations) + 1 module/domaine (users, keys, usage, datastore, projects, opendata…). Surface plate `db.<fn>` via __init__

├── auth_hooks.py     # current_user_sub_from_token() pour le contexte tool
└── config.py         # require_env

deploy/
├── oto-mcp.service       # systemd, User=root, /opt/oto-mcp, port 9103
├── Caddyfile.snippet     # mcp.oto.ninja → 9103 (pas de bearer-gate, masquerait WWW-Authenticate)
└── DEPLOY.md             # procédure DNS + Caddy + systemd + Claude.ai

```

L'extension Chrome (Oto Companion) vit dans `oto-app/extension/` (repo
`otomata-tech/oto-app`, monorepo des fronts). Elle parle au backend via REST :
`POST /api/settings/linkedin` + endpoints `/api/whatsapp/pair/*` (SSE).

## Couches (ADR 0004 — topologie réversible)

oto-mcp porte aujourd'hui 4 métiers ; ils sont des **couches à frontière à sens unique** (ADR 0004) :

- **backend-core** (le centre) : `db`, `credentials_store`, `org_store`, `access`, `crypto`, `connectors`, `auth_hooks`. Identité (`sub`), coffre, orgs, grants/quotas, résolution.
- **adaptateur MCP** : `server`, `tools/*`, `middleware`, `tool_visibility`.
- **adaptateur REST** : `api_routes`.
- **runtime connecteurs** : `tools/*` (in-process) + `tools/remote` (forward bridges).

**Règle** : adaptateurs + runtime → dépendent du backend-core, **jamais l'inverse** ; et ils l'appellent **par interface** (`access.resolve_*`), pas par accès table croisé — pour qu'un seam puisse devenir un service (broker de credentials) sans réécriture. ✅ Le seam **résolution** (le candidat broker) est consolidé dans `access` : `resolve_api_key` / `resolve_credential_fields` / `resolve_crunchbase_session`. C'est la frontière qui doit rester nette (elle peut devenir un service). `tools/meta` (visibilité) et `tools/datastore` (partage) appellent `db` en direct, et **c'est OK** : par le principe ADR 0004 (« pas de discipline d'interface sans force ») ils ne sont pas des candidats-services → pas de reroute dogmatique.

### Couche capacité (`oto_mcp/capabilities/`, ADR 0009)

Pour les opérations exposées sur **deux faces** (MCP + REST), arrêter de câbler les adaptateurs 2× à la main (drift de surface + autz divergente — ex. `oto_use_org` jadis absent en REST, IDOR cross-org). Une **capacité** = un descripteur co-déclaré : `handler` core + `Input` pydantic (seule validation) + règle `authz` **obligatoire** + bindings `mcp`/`rest` (multi-binding possible). Les adaptateurs `_mcp_adapter`/`_rest_adapter` **bouclent** sur `registry.CAPABILITIES` et appliquent **validation → autz → handler** — et la validation **REFUSE un champ inconnu** (`_rest_adapter` : clés reçues ∪ query ∪ path vs `Input.model_fields` → 400 `unknown_fields` nommant l'excédent ET les attendus). Pydantic ignore par défaut, donc un client qui se trompe de FORME recevait 200 + repli silencieux : vécu 2× en une semaine (`aiark_company_search(account=)` avalé par le jeton de contexte le 28/07, 72M lignes rendues sans erreur ; `{app, scope}` envoyés à plat au lieu de `params` le 05/08, scope retombé au défaut et retour OAuth chez le mauvais front). Garde au SEAM = les ~200 routes couvertes d'un coup, **sans allowlist** (tripwire `test_rest_rejects_unknown_fields.py` : une exception rouvrirait le trou). Face MCP non concernée (FastMCP valide contre le schéma déclaré) ; le refus est un `AuthzDenied` neutre traduit par chaque face (`McpError` / `json_error`+CORS). `authz` = combinateurs fermés (`SUB_ONLY`, `ORG_MEMBER`, `ORG_ADMIN`, `ORG_MEMBER_OF`, `PLATFORM_ADMIN`, `SUPER_ADMIN`, `ORG_ADMIN_OF`, `GROUP_MEMBER_OF`, `GROUP_ADMIN_OF`) — `ORG_MEMBER`/`ORG_ADMIN` scopent l'**org active** (lecture/écriture self-service `/api/me/*`), les `*_OF(field)` une org/groupe ciblé par id de path. Schéma MCP **plat** via `apply_flat_signature` (gotcha pydantic single-param, cf. memory). **Écho `_org`** (2026-07-02) : `_mcp_adapter` injecte `_org` `{id, name}` dans tout payload dict de capacité org-sensible (`ctx.org_id` posé) — le client voit l'org effective à chaque réponse (désambiguïse post-`oto_use_org` ; face MCP seulement, le REST connaît son contexte). Montés dans `server._build_mcp` + `api_routes.make_routes` (no-op si registre vide). **Domaines orgs + doctrine/instructions 100% migrés** : orgs (use_org, membres, secrets, create, lectures) → 100% en capacités, `api_routes_orgs.py` supprimé ; doctrine (`capabilities/orgs_instructions.py` : get/list/set/delete/versions/revert/usage membre `/api/me/instructions*` + outils `oto_*_doctrine`, et palier admin cross-org `oto_admin_*_doctrine` / `/api/admin/orgs/{id}/instructions*`) — `tools/orgs.py` supprimé, bloc doctrine d'`api_routes.py` retiré. ⚠️ Handler async supporté par les deux adaptateurs (`inspect.isawaitable`) ; le manifeste `referenced_tools` (ADR 0014) résout l'instance FastMCP via `tool_registry.bind(instance)` (posé au boot dans `_build_mcp`). **Domaine user-admin migré** (`capabilities/users_admin.py`) : retrouver/lister un user (`oto_admin_list_users`, filtre `query`), fiche (`oto_admin_user_detail`, par email **ou** sub), rôle (`oto_admin_set_role`), grant de clé plateforme user **et** org (`oto_admin_grant_key`/`oto_admin_grant_org_key` + revoke), option payante comp (`oto_admin_set_option`) — les handlers REST écrits main correspondants ont été retirés d'`api_routes.py` (mêmes chemins servis par les capacités → dashboard inchangé). Donne la face MCP au **setup complet d'un compte depuis Claude**.

**Console admin consolidée par concept (`*_op`, 2026-06-25, commit 92462fe).** Les outils admin ci-dessus sont fusionnés de **36 → 12 `oto_admin_*`** — un outil par objet métier, verbe en param `op` : `oto_admin_{org,org_member,user,access,key_grant}`. Les handlers de domaine sont **réutilisés tels quels** (zéro duplication ; `capabilities/admin_console.py` construit l'`Input` spécifique et appelle `_create_org`/`_add_member`/…). Quand les paliers d'autz divergent dans un même outil (ex. `org` : create=`SUPER_ADMIN`, list=`PLATFORM_ADMIN`), le **combinateur op-aware `ADMIN_BY_OP({op: règle})`** (`_authz.py`) choisit la règle fermée selon `inp.op` → l'autz reste **déclarée au niveau capacité**, jamais redescendue dans le handler (esprit ADR 0009 préservé). ⚠️ Les faces **REST restent par-verbe** (idiomatique + dashboard) → l'autz d'un verbe fusionné est désormais déclarée 2× (MCP op-aware + route REST), même combinateur/handler dessous. **Règle de design — secret brut jamais en argument MCP** (il transiterait dans le contexte LLM) : la **pose** de secret (`set_org_secret`, `delete_org_secret`, `set_platform_key`, `set_quota`) est **dashboard-only** (binding `mcp` retiré, REST conservé) ; le MCP ne porte que les **droits/grants** (`oto_admin_key_grant`).

## Auth — Logto

JWT Logto **ES384** (défaut RS256 = tout rejeté), discovery RFC 9728 sur 401,
façade DCR self-service (`oauth_facade.py`) pour les clients sans DCR (Claude/ChatGPT/
Mistral). **Détail : `docs/auth-logto.md`** (gotchas, env, onboarding).

> **La face MCP accepte aussi un jeton d'API `oto_` (v1.57.0).** `_IatGatedVerifier._verify_api_token`
> essaie `db.verify_api_token` avant le JWT (DB **hors de la loop**), et un jeton reconnu rend un
> `AccessToken` porteur du `sub` de son émetteur — donc un vrai compte, avec son dashboard et ses
> connecteurs. Sans ce chemin, un runtime **non interactif** (Claude Tag dans Slack, une CI) n'avait
> que `client_credentials`, donc une app Logto par intégration, donc un compte machine orphelin
> (ni email, ni dashboard, org à poser par `PUT /api/me/active-org` faute d'UI). ⚠️ **Un jeton PORTÉ
> (`scopes`) est refusé ici** : son gate `token_scopes.authorize` raisonne sur méthode + chemin HTTP,
> notions absentes d'un appel MCP — l'accepter élargirait sa portée en silence. Fail-closed figé par
> `tests/test_mcp_api_token.py`. Procédure côté utilisateur = guide plateforme `claude-tag`
> (+ template public `otomata-tech/oto-claude-tag-template`, Claude Tag n'acceptant qu'un dépôt privé
> comme source de plugins).

> **Le verifier est un REGISTRE d'émetteurs, et le sub est qualifié par tenant (ADR 0052 L2).**
> `server._build_verifier` construit `issuer → (tenant, verifier)` (`tenancy.py`) : sélection par
> le claim `iss` **non vérifié** (il ne sert qu'à CHOISIR ; le verifier retenu revalide signature
> + `iss`). **`LOGTO_ENDPOINT_ALT` n'est plus un `fallback`** : c'est une entrée du registre sur
> le même tenant `oto` (deux émetteurs, un tenant). L'émetteur primaire vient de l'ENV, jamais de
> la base → l'auth canonique est DB-indépendante ; les tenants tiers viennent de la table
> `tenants` (registre construit **au boot** ⟹ un tenant neuf demande un restart).
> ⚠️ **Le tenant `oto` garde un sub NU** — l'AAD du coffre dérive du sub
> (`credentials_store._aad`) : le qualifier rendrait TOUS les credentials de prod
> indéchiffrables. Un tenant tiers a `"<slug>:<sub>"`, et **en aval le sub est une chaîne
> OPAQUE, jamais découpée** (`entity_id` ne se découpe qu'à son PREMIER `:`, et jamais au scope
> `user` où `entity_id` EST le sub — tripwire `tests/test_tenant_l2_sub_opaque.py`). Pour savoir
> de quel tenant relève un sub : `tenancy.current().tenant_of(sub)` (classement par préfixe).
> **Détail : `docs/auth-logto.md` §Registre d'émetteurs.**

> **⚠️ CUTOVER ADR 0040 (2026-07-06) — `.ninja`↔`.cx` inversés.** Désormais **PROD =
> `mcp.oto.cx`** (:9103, audience canonique `mcp.oto.cx/mcp`, dashboard `manage.oto.cx`) et
> **PREPROD = `mcp.oto.ninja`** (:9105, audience `mcp.oto.ninja/mcp`, dashboard `manage.oto.ninja`).
> DB découplée (backends inchangés, seuls domaines/audiences/dashboards ont basculé ; prod
> reste sur `otomata-main`). ⚠️ **Logto = 2 instances** : la vraie prod/preprod = **`auth.oto.ninja`**
> (creds SOPS `LOGTO_NINJA_MGMT_*`), PAS `auth.oto.zone`. Les mentions `mcp.oto.ninja=prod`
> ailleurs dans ce fichier sont **antérieures au cutover**.
>
> **Coexistence multi-domaine (2026-07-02, contexte pré-cutover)** : `mcp.oto.cx/mcp` servait le
> MCP en plus de `mcp.oto.ninja` — env **`MCP_AUDIENCE_ALT`** (audiences canoniques secondaires,
> vide = no-op), resource Logto dédiée, PRM Host-aware (`config.mcp_audience_alt_hosts`).
> DNS mcp.oto.cx = grey+ACME direct box.
>
> ⚠️ **`MCP_AUDIENCE_ALT` est une LISTE (virgules) : ÉTENDRE, jamais remplacer.** Un
> `sed 's|^MCP_AUDIENCE_ALT=.*|…|'` écrase les audiences déjà déclarées — sans erreur au
> boot, le service démarre : la casse ne se voit qu'au premier `invalid_token` d'un client.
> Vécu 03/08 (Tulina) : la preprod portait `mcp-canari.oto.ninja/mcp`, l'écraser aurait coupé
> le canari. Chaque environnement a SA liste (`/opt/oto-mcp/.env` ≠ `/opt/oto-mcp-canari/.env`) :
> poser une audience sur l'un ne la pose PAS sur l'autre — le symptôme est alors « ça marche en
> prod, pas en preprod ». Même règle pour tout env-liste partagé (`OTO_MCP_CORS_ORIGINS`,
> `MAILER_FROM_DOMAINS`, SPF, redirect URIs OAuth) : lire la valeur, y ajouter, réécrire.

## Rôles + résolution de clé API

3 paliers `member < admin < super_admin` (accès admin UI). Résolution de clé par
appel : `clé membre (sub, org) > group_secret > org_secret > platform_grant` (chemin
platform gaté sur `auth_modes`). **Détail : `docs/roles-and-resolution.md`** (paliers,
grants/quota, platform keys, providers byo-only).

> **Scope MEMBRE (ADR 0033)** : plus de credential per-user org-agnostique — la clé
> BYO est keyée `(sub, org)` (coffre `entity_type='member'`, AAD lié à l'org ; google
> + unipile inclus, seuls les mounts oauth fédérés restent scope user). L'org de scope
> = seam `current_org`, à la pose comme à la résolution.
> **Détail (helpers db, state HMAC google, migration) : `docs/roles-and-resolution.md` §Scope MEMBRE**.

**Seam substrat (ADR 0024)** : `access.resolve_credential(provider, want, sub?)` marche la cascade UNE fois → `ResolvedCredential{key, is_platform, mode, config, fields}` ; `resolve_api_key`/`resolve_credential_fields` = vues minces dessus (les ~15 tools keyed inchangés). `config` = **config non-secrète appariée à la clé gagnante** (endpoint/host : `dsn` unipile, `base_url` n8n/make, `data_center` zoho — `config_fields` `secret=False` ∪ meta public) → ne JAMAIS recâbler un résolveur d'endpoint par-connecteur. `access.credential_mode_for(sub, provider)` = le `mode` sans déchiffrer (détection BYO = `mode ∈ {user,group,org}`, jamais un check user-only). **La cascade elle-même = walker unique `access.walk_cascade`** (sonde présence /api/me vs fetch résolution) — ne jamais la recopier dans un call-site, contrat gardé par `test_cascade_walker.py` ; détail : `docs/roles-and-resolution.md` §Walker. ⚠️ **Le dashboard en porte un MIROIR d'affichage** (`lib/keyStack.ts`, oto-dashboard — il annonce à l'utilisateur quelle clé prendrait le relais s'il retire la sienne) : aucun test ne relie les deux repos, donc changer l'ordre des paliers ou ce qui est lu à chacun (ex. le groupe **actif** seul) ne casse rien — ça fait **mentir l'UI**. Vécu 04/08 : la pile annonçait comme relais des clés d'équipes non actives, que la cascade ne lit jamais.

## REST API (consommée par le dashboard / oto.ninja)

Endpoints `/api/*` (compte, settings, orgs, admin, datastore…), même
`JWTVerifier` que `/mcp`. **Inventaire : `docs/rest-api.md`**.

> **Descriptif dérivé + jetons portés (03/08).** `GET /openapi.json` (et
> `/api/openapi.json`) sert un OpenAPI **dérivé** du registre de capacités + de la table
> de routes vivante (`openapi.py`) — sans auth, `/api/admin/*` exclu. Il existe parce que
> la surface était *indescriptible* : après l'ADR 0047, le verbe vit dans le corps (`op`),
> donc un intégrateur qui sonde `/api/projects` tombe sur 404 et conclut « pas de REST »,
> alors que `POST /api/me/projects {"op":"list"}` sert tout le métier projet. Côté sécurité,
> deux crans sur les jetons `oto_` : leur **gestion** exige une session interactive
> (`allow_api_token=False` sur `/api/me/tokens*` + miroirs admin — un jeton ne fabrique
> plus de jeton, une fuite n'est plus auto-entretenue), et un jeton peut naître **porté**
> (`token_scopes.py`, `user_api_tokens.scopes`) : deny-by-default borné à des tableaux
> nommés en read/write. `scopes` NULL = jeton historique, inchangé. Depuis le 03/08 la
> portée nomme aussi des **projets** (`{"projects": {"12": "read"}}`), servis par
> `GET /api/me/projects/{id}` — la forme POST porte sa cible dans le CORPS, donc aucune
> portée ne peut la borner : **ce qu'un jeton porté atteint doit se lire dans le chemin.**
> C'est la règle à garder en tête avant d'ouvrir une nouvelle surface aux intégrations.

⚠️ **CORS : la liste du code est MORTE en prod comme en preprod.** `_allowed_origins()`
(`api_routes.py`) n'est qu'un **fallback** — les DEUX box posent `OTO_MCP_CORS_ORIGINS`
dans leur `.env`, qui **écrase** la liste. Ajouter une origine au code, la déployer et
constater que rien ne change est un piège vécu (30/07, front Tulina) : le tag prod avait
été posé pour une raison inexacte. **Ajouter une origine = éditer l'env des deux box +
restart** (`/opt/oto-mcp/.env`, `/opt/oto-mcp-canari/.env`) ; le code ne sert qu'aux
environnements neufs. Diagnostic en 1 appel, sans lire le `.env` : `curl -X OPTIONS
https://mcp.oto.cx/api/mcp/catalog -H 'Origin: <x>'` → l'en-tête `Access-Control-Allow-Origin`
revient si l'origine passe. ⚠️ Ne pas déduire « c'est la liste du code » du seul fait qu'une
origine du défaut est acceptée : l'override en contient une copie.

## Browser automation & LinkedIn — substrat hébergé Browserbase (ADR 0026)

Plus AUCUN browser sur la box : les connecteurs d'**API privée cookie-bound**
(`brevo`, `crunchbase`, `pennylaneged`) passent par **Browserbase** (Chrome hébergé,
Context per-user = la session loguée au coffre, Live View pour le login interactif,
`run_fetch` same-origin). Connexion = dashboard (`browser_session.py`, un seul corps
REST+MCP, `login_url` obligatoire au register). LinkedIn = **Unipile** (tools/linkedin
supprimé) ; l'injection de cookie `li_at` côté serveur déconnecte l'user (#5).
S'y ajoute le connecteur **générique `browser`** (oto-private#79) : **lire** N sites
derrière login sans un connecteur par site — **un site = un compte du coffre** (host,
donc un Context par site), `browser_fetch` rend la page **complète** (≠ `run_fetch`,
tronqué à 400 c.), verify générique = cookies sur le host (+ échappatoire `force`),
`browser_eval` masqué par défaut.
**Détail (substrat, connecteurs, sécu, leçons empiriques) : `docs/browser-automation.md`**.

## SIRENE stock (DuckDB sur parquet INSEE — lu depuis S3/httpfs)

Stock complet (~43M établissements, parquet ~2GB) interrogé via DuckDB :
- **Source = Object Storage** (ADR 0002 résolu 2026-06-22) : la box dédiée n'est PAS
  co-localisée avec le parquet → `SIRENE_STOCK_PARQUET_PATH=s3://oto-media/sirene/StockEtablissement.parquet`,
  lu en **httpfs** (range reads, pruning de row groups). Creds DuckDB via env
  `SIRENE_STOCK_S3_{ENDPOINT,REGION,KEY_ID,SECRET,URL_STYLE}` (url_style=`path` pour
  Scaleway — `vhost` 3× plus lent). Le module accepte aussi un chemin local ou une URL
  `https://` publique. **Perfs box (2 vCPU)** : lookup point ~2s, scan filtré ~20-30s.
  ⚠️ Pour CHERCHER des boîtes (secteur/zone/taille), préférer **`fr_search`**
  (API recherche-entreprises indexée, <1s, filtre `categorie_entreprise` PME/ETI/GE) ;
  le parquet = lookups ponctuels + **bulk** (cf. ci-dessous) + énumération exhaustive >10k.
- Refresh : data.gouv republie mensuellement (URL datée → `deploy/refresh_sirene_stock_s3.sh`
  résout l'URL via l'API data.gouv puis push S3, à lancer sur otomata-0 ; **cron non installé** —
  le parquet bouge lentement, refresh manuel quand ça compte).
- Query layer : `france_opendata.sirene_stock` (lib PyPI `france-opendata[stock]`, **>=0.11** = support s3:///httpfs).
- MCP tools `fr_stock_*` (ex-`sirene_stock_*`, fusionnés dans le connecteur `sirene` le 2026-06-22 — même domaine entreprises FR, namespace `fr`) : **`fr_stock_enrich(sirens=[...])`** (bulk — sièges d'une LISTE en UN scan), `fr_stock_siege`, `fr_stock_etablissements`, `fr_stock_siret`, `fr_stock_search` (`sieges_only=True` = siège strict). Pendant parquet des `fr_*` live.
- REST `/api/sirene/{headquarters(POST,batch),siege,etablissements,siret,search,info}` (noms de routes **inchangés** — `oto-cli`/`oto-core` en dépendent ; orthogonaux aux noms MCP).
- Consommé par `oto-cli` (`SireneStock` HTTP client, oto-core >=1.8 — `get_headquarters_addresses` = 1 POST batch, plus N appels) — voir ADR 0001 + 0002 dans le privé `otomata-private`.

## Recherche transverse & KB projets (lot 3, plan JB — suivi oto-private#67)

**`oto_search`** (capacité `me.search`, MCP + `GET /api/me/search`) = LE verbe « retrouver »,
un seul chemin de code (`search.py` orchestration RRF k=60 · `db/search.py` SQL par source +
**expressions d'index = source unique index↔requête**, GIN d'expression, config `french` +
repli d'accents `translate`). Sources : pages/briefs/procédures/guides (passages, ts_headline
sur la saisie BRUTE) ∪ tableaux/fichiers/connecteurs (conteneurs, matchés en mémoire).
⚠️ **Deux grains distincts pour un tableau** : `tableau` = le CONTENEUR, matché en mémoire
sur le seul **nom du namespace + les labels de colonnes** ; `ligne` = le CONTENU des
tableaux (#67 V2.1, `_match_rows`), FTS sur les lignes elles-mêmes. Chercher « tableau »
seul ne trouvera donc jamais une valeur DANS une ligne — c'est `ligne` qu'il faut. Un
**fichier** reste matché sur `filename+title+description`, **jamais son contenu**.
**Invariant « cherchable ⇔ lisible »** : docs/briefs/fichiers scopés
`ownership.accessible_project_ids` (factorisation du scoping d'`op=list` — JAMAIS
`can_access`, cross-org) ; **tripwire par source = critère de merge**
(`test_search_scope_tripwire.py`). Le catalogue connecteurs est INJECTÉ par la capacité
(pas d'inversion de couche). `oto_doc(op=search)` = rerouté, déprécié. Fichiers matchés sur
`filename+title+description` (jamais `summary`, colonne morte).

**Sémantique + RRF (20/07, LIVE preprod)** : fusion LEXICAL + SÉMANTIQUE des pages.
`embeddings.py` = client Mistral `mistral-embed` (1024) — **sync `embed_texts`** (worker, batch DÉCOUPÉ sous le
budget de tokens/requête : 400 « too many tokens overall » sinon ; cap ~16k ch/input)
+ **async `embed_query`** (chemin requête). Outbox `docs.embed_dirty` (marqué à
create/update, coût nul) + `doc_embeddings(halfvec(1024))` + index HNSW cosine ; worker
`embed_worker` (boucle de fond composée au lifespan, embed HORS event loop via
`run_in_threadpool`, idempotent par `content_sha`) draine. Handler `oto_search` ASYNC :
embed la requête hors boucle → `search.search(query_embedding=…)` ajoute la source
`page`/`matched_by='semantic'`, la fusion RRF DÉDUPLIQUE (kind,ref) + SOMME les rangs
(une page trouvée par les deux remonte ; passage lexical conservé). **Dégradation
gracieuse** : sans `MISTRAL_API_KEY` ou sur échec → lexical seul, jamais un prérequis.
pgvector 0.8.2 sur otomata-main (`CREATE EXTENSION vector` AVANT `_SCHEMA` car halfvec en
dépend). Le **golden set JB** cale désormais la QUALITÉ (plus le *si*).

**Se repérer** : `docs.description` (chapô ; fallback DÉRIVÉ À LA LECTURE `derive_description`,
jamais stocké) + `docs.position` (ordre curé, entiers ×16 ; `move_doc(parent?, position=INDEX)`
réindexe la fratrie ATOMIQUEMENT) + **épine** `oto_project(op=get, include=['spine'], from_doc?,
depth?)` bornée (N+2, plafond 200, compteurs `more`) — la carte que l'agent lit avant
`oto_doc(op=get)`, jamais `op=list` de tout. **KB d'org ancrée PAR ID** (`orgs.kb_project_id`,
claim optimiste anti-doublon, auto-réparation transfert/archive — le nom n'est plus un marqueur).
Le lien `project_links.target_type='doc'` est RETIRÉ ; relier des pages =
les **backlinks `[[…]]`** (Ship 4, LIVE) : résolus À L'ÉCRITURE (hook `db.create/update/
delete_doc` — JAMAIS capacité, `resolve_change` appelle db en direct), précédence projet >
KB (`db/backlinks.py`), table dérivée `doc_links` (CASCADE 2 côtés), `oto_doc op=backlinks`
= « Cité par » filtré accès. **Propositions modif+création + inbox** (Ship 3, LIVE) : « les
lecteurs proposent » — un viewer (lecture sans écriture) qui crée/modifie obtient une
PROPOSITION (`doc_change_requests`, `doc_id` nullable + `project_id` + emplacement + CHECK) ;
le dispatch `docs.py` route resolve/list/create-proposal sur request_id/project_id **AVANT
le gate doc_id** (une création doc_id NULL était sinon inatteignable) ; `me.inbox`
(`GET /api/me/inbox`, 2 voies À traiter/Récent, 200-vide sans org).

**Seam `pending_action`** (`status_hints.py`, patron connector_verify) : un connecteur à
connexion en deux temps enregistre un hook « quelle étape manque ? » → `ProviderStatus.
pending_action` (fail-open) que le front rend tel quel en verdict+CTA. La spécificité vit
DANS le module connecteur (unipile : « Connecte un canal »), jamais dans le modèle commun.

## Datastore (spine natif PG, ADR 0016)

Spine plateforme de stockage structuré (PG/JSONB natif, plus Google
Sheets). Surfaces : tools `data_*` (MCP) + REST `/api/datastore/*` ; OAuth Google
per-user (Gmail/Tasks, multi-compte) câblé ici. **Détail : `docs/datastore.md`**
(surfaces, OAuth multi-compte + scopes restricted/CASA, setup GCP, env vars).

## Propriété de ressource — primitive `ownership` (ADR 0030)

`ownership.py` = seam unique : ressource possédée par `(owner_type∈{user,group,org},
owner_id)` + partages `resource_grants` (deny-by-default). **Deux plans jamais
confondus** : `can_access` (contenu, privacy by default) vs `can_govern` (gouvernance,
escalade roles.py). ⚠️ **Une LISTE de contenu scope sur `active_owner(current_org)`,
JAMAIS `owner_pairs()`** (union de toutes les orgs = fuite fail-open ; tripwire
`test_owner_scope_tripwire.py`). Plus de « perso » : tout user a une org perso dédiée
(`orgs.personal_of`), défauts de création = org active.
**Détail (datastore pilote, oto_resource, migration, abolition du perso) : `docs/ownership.md`**.

> **Partage unifié audience × rôle (ADR 0048).** Le grant porte un **rôle**
> `resource_grants.role ∈ {viewer, editor, manager}` (`permission` read/write reste la
> projection CONTENU dérivée → SQL du plan contenu inchangé). `manager` (gérant) rend la
> **gouvernance grantable** : `can_govern = owner ∪ grant gérant ∪ escalade roles.py` ; le
> **transfert** reste `can_transfer = owner ∪ escalade` (jamais un gérant). Surface unique
> `oto_resource op=share` : axe **audience** (person/team/org→grant ; public/secret→publication
> projet ; private→dépublier) × **rôle**. Rétro-compat `permission` en entrée.

## Projet — couche d'organisation (ADR 0030/0032)

Conteneur de travail **possédé** : brief + liens typés (`project_links` : tableau/
procédure/connecteur/**doc** — `doc` = une page Documents attachée) + docs en arbre. Capacités `oto_project`/`oto_doc` ;
partage/transfert via `oto_resource`. S'y greffent : **livraison client cascade**
(#52), **endpoint MCP + partage navigable par projet** — un projet publié est servi sur
son sous-domaine dédié, modes **anonymous** (`<slug>.mcp.oto.cx`, sans login + listé) /
**secret** (`<slug>.share.oto.cx`, URL non devinable = **UI navigable** lecture seule des
procédures/tableaux/docs, rendu server-side `share_ui`, + MCP au path `/mcp`) / **org**
(authentifié) ; sonde credential-less **non bloquante** → `mcp_unresolvable_tools` en
warning ; annuaire oto.ninja/apps. (Le partage public **chiffré** `/p/p` a été retiré,
supplanté par ce partage navigable live.) La page navigable (`share_ui`) est un **canal
d'acquisition** : hero « brancher », connecteurs en pastilles (logo + tooltip + lien fiche),
tableau riche (recherche/tri/filtres), et CTA **« Ajouter à mon Oto »** → capacité
`me.import_project` (`POST /api/me/projects/import`) qui **forke un projet publié par slug**
dans l'org active (structure only, jamais de credentials ; idempotent via `projects.copied_from`).
**Détail : `docs/projects.md`**.

## Messagerie & LinkedIn (Unipile)

Tools `{whatsapp,telegram,instagram,messenger,twitter}_chat` + **`linkedin_unipile_*`** = **Unipile
hébergé** (factory channel-agnostic, `account_id` per-membre `(sub, org_id, provider)`
ADR 0033, no-fallback anti-usurpation). Mode plateforme (clé partagée + grant + option comp),
DSN par credential, sélecteur d'identité, **comptes partagés autorisés** (#55, grants
revalidés à chaque appel, jamais de repli silencieux).
**Détail : `docs/unipile.md`**.

> **Renommage + consolidation LinkedIn (10/08, #279)** : `unipile_*` → **`linkedin_unipile_*`**,
> 38 tools → **8 à `op=`** (search · facets · profile · chat · post · network · account · job).
> Le namespace porte la CAPACITÉ suffixée du FOURNISSEUR (ADR 0010 §Amendement) — les 4 autres
> canaux du même connecteur le faisaient déjà (`whatsapp_*`…), LinkedIn était l'exception ; et
> `linkedin_*` (AI Ark, donnée achetée) n'est pas substituable à la session opérée, donc aucune
> des deux ne prend le nom nu. ⚠️ **`namespace_of` résout au plus long préfixe DÉCLARÉ au
> registre**, plus au 1er token — sans quoi `linkedin_unipile_*` tomberait sous le connecteur
> `linkedin` (mauvais credential/activation/sélection). Additif (aucun autre namespace n'est
> multi-token), tripwire `tests/test_linkedin.py`. `unipile_connect_start` garde son nom
> (multi-canal ⟹ hors capacité ; cible = `oto_connector op=connect`).
>
> **Lots 2-3 (10/08, même issue)** : les 5 autres canaux passent à `{whatsapp,telegram,
> instagram,messenger,twitter}_chat(op=list|read|send)` — 15 → 5, factory commune, le canal
> reste dans le NOM (trouvabilité) ; et **le connecteur `linkedin` est DÉPOSÉ** au profit
> d'`aiark`, dont les tools deviennent `linkedin_aiark_*` (6 → 3 : `search` op=people|companies,
> `person` op=export|reverse|mobile, `credits`). Les deux connecteurs étaient le même vendeur
> et le même client `AiArkClient`, ne différant que par le mode d'auth = une distinction
> d'INSTANCE (ADR 0038/0044 §F), qui coûtait de poser deux fois la même clé pour un seul pool
> de crédits (ADR 0024). Rien à migrer au coffre : aucun grant n'y était posé, ses 5 tools
> étaient **montés et inopérants** depuis leur mise en service. `linkedin_aiark_credits` REFUSE
> en mode plateforme (le solde du pool oto n'est pas celui de l'appelant). Domaine complet :
> **62 → 17 tools** ; catalogue **665 → 619**.
> ⚠️ **Reste à faire au tag prod** : migrer `user_selected_connectors` (119 lignes `linkedin`
> → `aiark`, dédoublonnées) — la DB est partagée preprod/prod, la migrer avant le tag
> retirerait le connecteur de 119 toolbox encore servies par l'ancien code.

> Le détail (cas limites, incidents, gotchas empiriques) a été migré dans **`docs/unipile.md`** — il n'a pas sa place dans une carte, et il y était devenu illisible.

## Monitoring des appels MCP

`ToolCallLogger` (middleware inliné `oto_mcp/calllog.py`, ex-lib otomata-calllog décommissionnée — contrat canonique dans le socle otomata-mcp) journalise chaque appel dans `tool_calls`
(`db.insert_tool_call`, best-effort, identité = `sub` du JWT via
`current_user_sub_from_token`).
**Détail : `docs/monitoring.md`**.

> **L'observabilité a trois étages (05/08).** Le journal servait deux sièges — « moi »
> (`/api/me/{activity-summary,calls}`) et « toute la plateforme » — et rien entre les
> deux : un responsable d'org n'avait que l'export brut d'audit (#67). `capabilities/
> org_monitoring.py` rejoue les lentilles à SON échelle : **`oto_org_monitoring(op=…)`**
> + `GET /api/orgs/{id}/monitoring/*`, autz `ORG_ADMIN_OF`. Scope = `tool_calls.org_id` /
> `usage_signals.org_id` (ce qui a été émis SOUS l'org), **jamais l'appartenance** — donc
> mêmes chiffres que l'export d'audit. `rest`/`funnel` ne descendent pas (santé d'infra,
> base entière) ; en échange une lentille propre à l'étage : **`adoption`**, membre par
> membre (actif / jamais actif / bloqué par un connecteur), qui part d'`org_members` —
> partir des appels rendrait invisible le membre à 0 appel, celui qu'on cherche.
> ⚠️ `call_id` est un BIGSERIAL : `op=call` compare l'org et rend le **même 404** qu'un id
> inexistant (idem `op=run`). Détail : `docs/monitoring.md` §Trois étages.

> **Investigation = une capacité, deux faces (02/08).** Les lentilles ont quitté les
> routes écrites main d'`api_routes.py` pour `capabilities/monitoring.py` (chemins REST
> `/api/admin/monitoring/*` **inchangés**, dashboard intact) et gagnent leur face MCP :
> console **`oto_admin_monitoring(op=…)`** (ADR 0047) — `summary` / `calls` / `call` /
> `run` / `runs` / `rest` / `connectors` / `funnel` / `gaps` / `tool_quality`. L'agent
> plateforme enquête EN SESSION, plus seulement via le dashboard.
> Le grain appel porte les axes de corrélation (`session_id`, `run_id`, `org_id`,
> `client_id`) et se filtre dessus, + `min_duration_ms` (appels lents, chasse aux gels
> mono-loop) et `error_contains`. `sub` accepte email OU sub.
> **`tool_calls.sentry_event_id`** relie la ligne d'audit à son traceback (posé par
> `SentryToolErrorMiddleware`) — fin du « chercher à la main par user.id ».
> ⚠️ Ces colonnes dépendent de **l'ordre des middlewares** (cf. §Conventions).
> ⚠️ **`client_id` n'identifie PAS le front d'où vient l'utilisateur** : c'est le client
> OAuth du jeton, et un même id couvre des orgs de produits différents (07/08 : le même
> `client_id` sur des orgs Tulina ET sur `movinmotion`/`Mūcho`/`Audiens`, pendant que
> d'autres appels des mêmes orgs sortent avec `client_id` NULL). Le backend ne stocke
> **nulle part** par quel front un compte est arrivé — d'où les colonnes `orgs.front_*`
> (§Email). Le piège est qu'un échantillon de 2 lignes semble confirmer le contraire :
> énumérer (`GROUP BY client_id`) avant d'en tirer une population.

⚠️ **Ne trace QUE les invocations d'outils MCP** —
pas la connexion du connecteur, pas le `tools/list`, pas les appels REST/dashboard.
Donc **compte actif ≠ usage** : un user qui a un compte (table `users`) mais 0 ligne
`tool_calls` n'a jamais déclenché d'outil (connecté-mais-idle, OU handshake OAuth du
connecteur jamais réussi → diagnostiquer via `journalctl` 401). Vécu 2026-06-22 (JB,
Julien : comptes actifs, 0 appel ; le monitoring marchait, eux n'avaient rien invoqué).

## Error tracking (Sentry)

Exceptions backend → **Sentry SaaS** (gaté `OTO_SENTRY_DSN`, no-op si absent →
le serveur boote sans). Deux captures : **500 des routes REST `/api/*`** via
l'intégration Starlette (auto) ; **exceptions des tools MCP** via
`SentryToolErrorMiddleware` (`sentry_setup.py`) — une erreur de tool est une erreur
JSON-RPC en **HTTP 200**, invisible à l'intégration Starlette, donc capturée là où
l'exception est vivante (vrai traceback, tag `mcp.tool` + `user.id=sub`). RGPD :
`send_default_pii=False`, **jamais** les args d'appel dans l'event. `before_send`
**droppe les 4xx amont** (`HTTP 4xx` d'une API tierce = input rejeté, pas un bug
backend). Env box : `OTO_SENTRY_{DSN,ENV,RELEASE,TRACES_SAMPLE_RATE}` ; région **EU**
`de.sentry.io` (org slug `otomata-vz`). Surveillance/triage = doctrine oto
`surveillance-erreurs` (token API en SOPS `sentry_api_token`).
Un appel sur un tool HORS toolbox de session (la visibilité filtre `tools/list`,
pas `tools/call`) = erreur **GÉRÉE actionnable** `tool_not_mounted`
(`error_taxonomy` : oto_call immédiat / `oto_connector op=select`), droppée de
Sentry — plus jamais un « Erreur interne du serveur » opaque (vécu 16/07, #224/#225).

## Onboarding = un projet « Découverte » (ADR 0032 §7)

**Plus de mode d'accueil spécial** (retiré le 2026-07-01) : pas de booléen `onboarded`,
pas de checklist dashboard, pas de tool d'onboarding scripté. L'onboarding est **un
projet** comme un autre — un projet « Découverte » porteur d'un brief d'accueil, **semé
à la création de l'org perso** (`discovery.seed_for_org`, appelé par
`org_store.ensure_personal_org` sur la branche création, best-effort). Il remonte à
l'agent via la ligne « Projets récents » du bloc C des instructions (`instructions.py`) ;
l'agent l'ouvre (`oto_use_project`) et déroule l'accueil depuis son brief.

**La fiche « situation avec oto » reste** (qui est l'user, son métier, ses objectifs, son
CRM, les connecteurs voulus, son ton) — découplée de l'accueil, c'est un data model libre
relu à chaque session :
- **Capacité `me.profile`** (`capabilities/profile.py`, ADR 0042 §Convergence des surfaces) :
  UNE implémentation, deux faces — `oto_profile(op="get"|"update", fields=…)` côté MCP
  (spine, hors gate, **toujours visible** via `PROTECTED_TOOLS`) + `GET`/`PUT /api/me/profile`
  côté dashboard. ⚠️ Divergence VOULUE entre les faces : `op=update` (agent) **filtre les
  valeurs vides** — un agent n'efface pas la fiche par mégarde ; le `PUT` (humain) écrit tel
  quel, donc vider un champ passe. Réponse unique `{profile, updated_at, fields, missing}`.
  *(Avant le 2026-07-28 : tool écrit à la main `tools/profile.py` **doublé** d'une capacité
  REST — deux contrats sur une donnée, et l'éditeur dashboard orphelin. Supprimé.)*
- DB : table `user_account_profile(sub PK, profile jsonb, created_at, updated_at)`
  (`db.get_account_profile` / `db.update_account_profile`). **Injectée au handshake**
  (bloc C, section « Ce que tu sais de l'utilisateur ») → enfin utilisée, plus seulement
  collectée. N'est plus exposée sur `/api/me` (le bloc `onboarding` a été retiré).

`tools/whoami.py` (spine, chargé explicitement dans `register_all`, hors gate
d'activation, **toujours visible** via `PROTECTED_TOOLS`) expose `oto_whoami()`
(lecture) — l'**identité MCP courante** sous laquelle Claude agit : compte (`sub` +
email + rôle plateforme) × **org active** (id/name/rôle) × **groupe actif**, plus un
résumé des connecteurs configurés et l'ancre de la KB d'org. C'est le pendant agent du badge
« identité MCP » du dashboard ; à appeler pour confirmer le contexte avant une action
sensible. Pour basculer : `oto_use_org`.

## Automatisations — déclencher une routine Claude Code (v1.73.0)

Connecteur `routine` (`routine_fire.py` + capacité `me.automation.fire`, MCP
`routine_fire` / REST `POST /api/me/automations/fire`) : **une instance = une routine**
hébergée chez Anthropic (`routine_id` + jeton de déclenchement en `credential_fields`),
parce que le jeton `/fire` est scopé par Anthropic à une seule routine. L'appel ne bloque
pas — il crée la session et rend son URL ; le résultat se lit **dans la session**.
Le `text` arrive à l'agent enveloppé `<routine-fire-payload>` étiqueté DONNÉE NON FIABLE
(le prompt de la routine doit opter pour le lire) ⟹ passer une **référence**, jamais
l'enregistrement. Montage complet côté utilisateur = guide plateforme
**`procedure-en-routine`**.

⚠️ **Ce connecteur relaie, il n'apporte rien d'autre** : un tiers qui sait faire un POST
appelle `/fire` en direct. Son seul cas réel est *un agent en conversation qui déclenche
une automatisation*. Il ne vaudra plus que ça tant qu'oto ne fait rien entre les deux
(tracer les tirs, router selon l'événement, dédupliquer). **Aucune API publique de
création de routine ni de génération de jeton** — le provisionnement reste manuel, par
construction ; l'état vide de la page Automatisations du dashboard l'explique.

## Boucle d'usage (ADR 0017)

Flux d'événements de session unifié : calllog (involontaire) + feedback volontaire
d'agent (`feedback`, signal=tool_feedback|gap) + runs / déroulés (`run_start/finish`,
`doctrine` optionnel → doctrine nommée ou run one-shot). **Détail : `docs/usage-loop.md`**.

> **Runs persistés (#50, amende le « state-only » d'ADR 0017).** La métadonnée
> sémantique d'un run (label / doctrine / outcome) vit désormais dans la table `runs`
> (`db.insert_run`/`finish_run`/`recent_runs`) — la pile session-scopée de
> `doctrine_run.py` reste la **source du run actif** (stampe `tool_calls.run_id`),
> `run_start`/`run_finish` y ajoutent la trace durable (best-effort, off-loop). Sert
> l'anticipation du contexte injecté (instructions bloc C) + la boucle d'usage dashboard.

## Email (envoi per-org, par connecteur)

Deux connecteurs **BYO-org** : `scaleway` (API TEM directe, fields — domaine garanti
par Scaleway) + `resend` (BYOK). `email_send` = spine qui route
`sender→connecteur→transport` (`EMAIL_CONNECTOR_TRANSPORT`) ; config
`orgs.email_settings` par connecteur (senders + quiet hours) ; envoi différé
(`scheduler.py`, quiet hours 20h–8h défaut). **Détail : `docs/email.md`**.

> **Front qui héberge l'org (invitations, 07/08).** oto-backend sert plusieurs produits
> depuis une instance (oto, Tulina) : deux colonnes `orgs.front_base_url` / `front_brand`
> (NULL = oto) portent le front d'une org, lues par `emit_invitation` — base du lien
> `/invitation/<code>`, marque du texte du mail, **et pas de magic-link** dès qu'un front
> tiers est posé (l'OTT est minté sur NOTRE Logto : il serait inerte sur l'émetteur dédié
> du tiers, soit un échec de connexion silencieux). **Dérivé de l'org CIBLE, jamais déclaré
> par l'appelant** — sinon c'est un champ d'API publique (REST + surface MCP) qu'il faudra
> retirer à l'arrivée de l'étage tenant (ADR 0052, où ces colonnes remontent d'un cran), et
> une invitation pourrait prétendre venir d'un front auquel l'org n'appartient pas. Les 3
> niveaux de la cascade en héritent sans rien porter. La marque s'arrête au TEXTE :
> l'expéditeur reste `_MAIL_FROM`, un domaine d'envoi tiers supposerait sa vérification TEM.
> ⚠️ Aucune surface n'édite ces colonnes (UPDATE à la main) : une nouvelle org sous front
> tiers naît donc sous marque oto tant que personne ne la renseigne.

## Visibility per-user

`UserDisabledToolsMiddleware` (`middleware.py`) applique au handshake `initialize` les visibility rules natives fastmcp (`disable_components` via `_visibility_rules` session state). Plus de filtrage manuel `on_list_tools`/`on_call_tool` — fastmcp émet `tools/list_changed` automatiquement quand les rules changent. Le **calcul** de la denylist `(sub, org active)` + son application vivent dans **`session_visibility.py`** (`compute_hidden_tools` / `apply_session_visibility(ctx, sub, *, reset=…)`), partagés entre le middleware (handshake) et le **refresh à chaud** post-bascule.

Source de vérité = tables PG `user_disabled_tools(sub, tool_name)` (négatif) + `user_enabled_tools(sub, tool_name)` (override positif). **Les presets de tools (snapshots nommés + baselines ALLOWLIST org/équipe) ont été retirés le 2026-07-03** (commit `3951a57` — masquaient tout ce qui n'était pas listé, lourd à maintenir : un tool ajouté après coup arrivait masqué par défaut pour toute baseline posée).

**Remplacés depuis par un DENYLIST org/équipe** (`capabilities/tools_visibility.py`) : un org_admin/chef d'équipe masque des tools SPÉCIFIQUES par défaut pour son org/équipe (`org_disabled_tools`/`group_disabled_tools`) — le reste, y compris les tools futurs, reste visible par défaut. Additif entre paliers (union à la lecture) : une équipe ne peut jamais RÉVÉLER un tool que l'org a masqué. Gouvernance de visibilité, PAS une barrière de sécurité (ADR 0031, même esprit que `DEFAULT_HIDDEN_TOOLS`) : `user_enabled_tools` (override perso positif) lève TOUJOURS ce masquage, même échappatoire qu'un masqué-par-défaut plateforme. Calculé fail-open **indépendamment par palier** dans `session_visibility.compute_hidden_tools` (`access.org_admin_hidden_tools`/`group_admin_hidden_tools`) — un hoquet DB sur l'équipe ne prive pas l'org de son denylist. Surfaces : MCP + REST `GET/PUT/DELETE /api/{orgs,groups}/{id}/tools/{name}?/hidden`.

**Sélection par membre = régime NOMINAL « non-sélectionné = masqué » (ADR 0019/0050).** La toolbox d'un membre = les connecteurs qu'il a **installés** (`user_selected_connectors`, per (sub, org)). Au premier profil d'un (sub, org), `session_visibility` seed le socle `providers.DEFAULT_ACTIVE_CONNECTORS` ∩ exposé — **VIDE depuis le 16/07** (décision produit : un nouveau compte démarre SANS connecteurs installés ; l'agent guide depuis les tools spine — `oto_connector` op=list/select, `oto_call` — et le catalogue injecté au bloc A) ; tout l'exposé = library installable (capacité `connectors.select`, dashboard). Les pairs pré-0050 ont été backfillés une fois avec leur visible d'alors (`connector_selection.backfill_preexisting`, sentinelle `#adr0050-backfill`). Un connecteur activé pour l'org APRÈS le seed arrive dans la library, pas dans la toolbox. Le grain CONNECTEUR `default_hidden` et les flags `OTO_CONNECTOR_SELECTION_*` ont été **retirés** (0050). **Masqués par défaut, grain OUTIL** (`is_default_hidden` = `DEFAULT_HIDDEN_TOOLS` seul : `email_send`, `fr_egapro_declaration`) : self-activables. Règle effective (`is_tool_visible`) : override positif prime > désactivé > masqué par un admin (denylist org/équipe ci-dessus) > masqué-par-défaut plateforme > visible. `oto_enable_tool` pose l'override, `oto_disable_tool` le lève (même logique côté REST `/api/me/tools/{name}`). **Stdio local (sub=None) = accès complet**, le masquage ne vise que le multi-user. Sortir un connecteur du départ = ne PAS le mettre dans le socle `default_active` ; un tool isolé = `DEFAULT_HIDDEN_TOOLS`.

Méta-tools exposés (`tools/meta.py`) : `oto_list_my_tools`, `oto_disable_tool`, `oto_enable_tool`, `oto_call`, `oto_tool_schema`. **`PROTECTED_TOOLS`** (`tool_visibility.py`, source unique) = quatre familles jamais masquables (default-hidden inclus) **ni désactivables** : méta-toolset + identité (`oto_list_my_tools`/`oto_enable_tool`/`oto_whoami`/`oto_profile`), échappatoires de contexte (`oto_use_org`/`oto_clear_org`/`oto_list_orgs`/`oto_use_group`/`oto_clear_group` — anti-lockout, vécu Sentry 2026-06-30), boucle d'usage (`feedback`/`run_start`/`run_finish` — mandatés par les instructions plateforme ADR 0017 : un toggle qui les masque rend le gap invisible), **dispatch universel** (`oto_call`/`oto_tool_schema` — ADR 0036 : appeler par son nom un outil NON listé (FOD, connecteur non activé) le temps d'un appel, sans muter la visibilité ; exécution par `Tool.run` HORS middleware → gates call-time intactes + rédaction ré-appliquée via `redaction.py`). Garde des deux faces (2026-07-02) : `oto_disable_tool` refuse, `POST /api/me/tools/{name}` → 400 `protected_tool` ; `GET /api/me/tools` expose `protected:bool` (toggle inerte dashboard).

**Refresh à chaud de la toolbox sur bascule de profil** : une capacité qui change le profil de visibilité déclare `refresh_visibility=True` (`Capability`) ; l'adaptateur MCP (`capabilities/_mcp_adapter.py`) rejoue alors `apply_session_visibility(reset=True)` sur la session **courante** après le handler → `tools/list_changed` live. Posé sur `org.use_org`/`org.clear`/`org.create`/`org.set_home` + `group.use`/`group.clear`/`group.set_home`. Donc **`oto_use_org <org>` recharge la toolbox dans la conversation en cours** (les credentials, eux, basculent déjà — `resolve_api_key` relit l'org **via le seam `current_org`** à chaque appel, cf. §ADR 0023 ci-dessous).

**Limite connue** : ça ne vaut QUE pour la face MCP (même session). Un toggle/bascule via **REST** (dashboard) passe par une connexion séparée → ne notifie pas une conversation Claude déjà ouverte (visible à la prochaine session). Pousser dashboard→session MCP demanderait un registre `sub → sessions actives` + push hors-requête (non fait).

## Org/équipe : session vs maison vs consultation (ADR 0023, amende 0015)

Le pointeur unique « org active » est scindé en **3 notions**, résolues par le **seam unique `access.current_org(sub)`** (mirroir `access.current_group(sub)` pour l'équipe) = `session ?? consultation ?? maison`. **TOUTE résolution d'action passe par ce seam** (`resolve_api_key`, visibilité `session_visibility`, field-filters, doctrine de groupe, `/api/me`, whoami, et l'injection `org_id` des règles d'autz `_authz`) — ne plus lire `org_store.get_active_org` en direct dans un chemin de résolution (**tripwire** `tests/test_org_seam_tripwire.py` : les call-sites légitimes de la maison sont figés en allowlist ; vécu 2026-07-02 — catalogue + toggles REST scopaient la maison, le switch d'org du dashboard était ignoré, fixé `25e9f22`. Pendant front : `orgScope.spec.ts` d'oto-dashboard interdit un `fetch` nu hors du client central qui injecte `X-Oto-Org`).

⚠️ **Ce seam est scopé sur l'ACTEUR courant** : session/consultation sont stockées **par requête**, le `sub` ne sert qu'au repli `home_org`. Donc `current_org(autre_sub)` renvoie le contexte du **requérant**, pas du tiers — **NE JAMAIS** l'utiliser (ni `status_for`/`has_option`/`credential_mode_for` qui en dérivent) pour calculer l'état d'un **tiers** (écran admin). Passer son org/groupe **explicitement** via le kwarg `org`/`group` (sentinelle `access._UNSET` = défaut `current_org`, self inchangé), source = `org_store.get_active_org(target)`. Bug vécu 2026-06-24 (fiche admin montrant l'option de l'org du requérant). L'état d'un user est par ailleurs souvent **per-org** (∈ N orgs) → préférer une vue par org (cf. `tools/unipile.admin_status_by_org`).

- **Org de session** (éphémère, MCP) — override posé par `oto_use_org`/`oto_clear_org` (devenus **session-scopés**, ne touchent plus la colonne) dans `session_org.py` (store sync keyé par `ctx.session_id` — `get_state` async est inutilisable depuis `resolve_api_key` sync). Meurt avec la conversation ; repose sur l'isolation des sessions claude.ai par conversation. **Pas de jeton rejoué par appel** (bracelet serveur, pas de discipline LLM).
- **Org maison** (`org_store.get_active_org`, ex-« active_org ») — défaut persistant des **nouvelles** conversations. Posée explicitement : `oto_set_home_org` (MCP) ou `PUT /api/me/active-org` (REST/dashboard) ; **jamais** par navigation dashboard.
- **Org de consultation** (REST, view-as) — header `X-Oto-Org` (équipe : `X-Oto-Group`), posé par le **middleware ASGI `api_routes.ViewAsMiddleware`** (brut, n'altère pas le streaming `/mcp`) APRÈS **validation d'appartenance** (anti-IDOR : `roles.is_org_member`/`can_read_group`) dans un contextvar lu par `current_org`. Le dashboard consulte n'importe quelle org **sans muter l'identité MCP** — mais « consultation » = **org de TRAVAIL de l'onglet, lecture ET écriture** (poser une clé, éditer les settings y atterrissent), gatée par le rôle réel dans l'org ciblée ; le seul mode read-only est le view-as USER ci-dessous.
- **« Voir en tant que » (axe USER, REST, lecture seule)** — header `X-Oto-View-As=<sub>` posé par le même `ViewAsMiddleware`, gaté **opérateur plateforme + cible existe + méthode GET** (mutations → 403 `view_as_read_only`). `_authenticate` renvoie alors le **sub cible** (param `apply_view_as`, contextvar `session_org.current_view_user`) → tout `/api/me/*` (capacités incluses) rend la vue de la cible. **REST-only** : le MCP ne lit jamais ce contextvar (zéro impersonation dans Claude). Front : bouton sur la fiche admin + bandeau `ViewAsBanner` (`lib/viewOrg.ts`).

**Invariant groupe⊂org dérivé** : un override/consultation d'org **sans** groupe explicite ⇒ niveau org (jamais le `home_group` d'une autre org) ; toute bascule d'org de session retire l'override de groupe. `/api/me` expose `active_org`/`active_group` (effectifs) **et** `home_org`/`home_group` (défauts) distinctement. `oto_whoami` montre l'org effective + `scope: home|session`.

## Agent readme (cumulable) & procédures — ex-« doctrines & instructions d'org »

Vocabulaire produit (unbundle 2026-07) : **agent readme** = prose libre **injectée à
chaque session**, cumulée du général au spécifique — **plateforme** (bloc A) → **org** →
**équipe active** → **user**. Les 4 étages vivent dans `guides` delivery='init' (0042) ET
**s'éditent par UNE surface** depuis le 28/07 (§Convergence des surfaces) : la capacité
`me.guide{,s}` — `oto_guide(op=…, scope=…, delivery='init')` en MCP, `/api/me/guides/{scope}/readme`
(+ variantes `/api/{orgs,groups}/{id}/…` pour viser une cible explicite) en REST. ⚠️ Le
routage `claude_md`→`guides` qui vivait DANS `org_store`/`group_store` est RETIRÉ : le store
de procédures ne sert plus le readme (`get_instruction` → None, `set_instruction` → ValueError),
les appelants qui le veulent lisent `guide_store.init_guide_body(scope, id)`. `me.agent_readme` +
`/api/me/agent-readme` + `db.{get,set}_user_readme` supprimés (table `user_agent_readme` laissée
en place — elle sert encore de source au backfill de boot ; son DROP est une migration à part). Chaque niveau passe par `_apply_vars`
({{org}}/{{user}}/{{équipe}}/{{connecteurs_actifs}}). **Procédure** = doctrine nommée
(skill), chargée à la demande — les identifiants de code (`_DOCTRINE_GET_TOOL`, tables,
`docs/doctrines.md`) gardent le mot doctrine. Prose opératoire versionnée par org,
**détail : `docs/doctrines.md`**.

> Le détail (cas limites, incidents, gotchas empiriques) a été migré dans **`docs/doctrines.md`** — il n'a pas sa place dans une carte, et il y était devenu illisible.

## Groupes (départements) & hiérarchie de droits (ADR 0012)

Une org se subdivise en **groupes** (départements/équipes) avec un **chef
d'équipe** (`group_role='group_admin'`). La gestion des droits est **centralisée**
dans `roles.py` (escalade descendante, source unique) :

```
platform_admin ⊇ org_admin ⊇ group_admin (chef) ⊇ member
```

Les combinateurs d'autz (`capabilities/_authz.py`) délèguent à `roles`
(`is_org_admin`, `can_admin_group`, `can_read_group`, `effective_group_role`) —
plus d'escalade recopiée à la main. Combinateurs : `GROUP_ADMIN_OF`,
`GROUP_MEMBER_OF` (en plus de `ORG_*`).

Un groupe **gouverne 3 ressources** par délégation de l'org (⚠️ **substrat unifié le
10/07/2026** — chantiers du cadrage objets/visibilité : plus de tables jumelles par
grain, le scope est une COLONNE ; migrations vivantes sur la DB partagée = playbook
**`docs/live-migrations.md`**) :
- **secrets partagés** — coffre `connector_credentials` (entity_type='group') ;
  cascade `resolve_api_key` = **user_key > secret groupe actif > secret org active > grant plateforme**.
- **doctrine & skills** — table UNIFIÉE `org_instructions` (`owner_type='group'`,
  `owner_id=group_id`, `org_id`=org parente ; ex-jumelle `org_group_instructions`
  DROPpée) ; `oto_procedure(op='get')` sert org **puis** groupe actif (complément,
  chaque skill taggée `scope`). Les procédures d'équipe ont un `id` (ownership 0030).
- **gouvernance de connecteur** — le chef d'équipe peut COUPER un connecteur et le
  RÉSERVER à des membres, pour son équipe seulement. **Invariant monotone** :
  l'équipe RÉTRÉCIT ce que l'org expose, jamais l'inverse (platform ⊇ org ⊇ group).
  Détail (paliers, fail-open indépendant, capacités) : `docs/groups-and-roles.md`.

**Groupe actif** : ≤1 par sub (`org_group_members.is_active`, index partiel),
**invariant** = appartient à l'org active. `set_active_group` pose aussi l'org
active ; `set_active_org` efface le groupe actif. `oto_use_group` /
`PUT /api/me/active-group` (+ `oto_clear_group` / `DELETE`).

Stores : `group_store.py` (miroir d'`org_store` au grain groupe). `org_store`
n'importe PAS `group_store` (SQL direct pour l'invariant org↔groupe → pas de
cycle). Surfaces : capacités `capabilities/groups*.py` (REST `/api/orgs/{id}/groups`,
`/api/groups/{id}*`, `/api/me/active-group` + MCP `oto_*_group*`). `/api/me`
expose `active_group`/`active_group_name`/`group_role` ; `providers[].mode` peut
valoir `group`. **Détails : `docs/groups-and-roles.md`.**

## Fédération MCP & comptes (otomata#16)

Deux mécanismes : **mount** (MCP distant fédéré, token OAuth per-user, pilote
atlassian) vs **remote** (bridge data-driven ADR 0003, token M2M d'org, pilote = un
connecteur remote client). **Plus aucun mount monté d'office** (fédération en
sommeil, masters atlassian/justicelibre OFF en prod ; le connecteur `memento` a été
RETIRÉ le 2026-07-30 — produit décommissionné, la mémoire est native `oto_kb`) : un mount suit le régime commun
d'activation (DB `connector_activation` ∪ env `OTO_MCP_MOUNTS_ENABLED`).
**Détail : `docs/federation.md`**.

## MCP Apps — UI rendue (SEP-1865)

Certains tools renvoient une **interface rendue** (carte/table dans un iframe
sandbox côté host : claude.ai, VS Code…) au lieu de JSON brut, via l'extension
MCP Apps (SEP-1865, stable). Implémenté avec **`prefab_ui`** (extra
`fastmcp[apps]`, déclaré dans `pyproject.toml` → installé par le `pip install -e .`
du deploy) : un tool `@mcp.tool(app=True)` renvoie un composant `prefab_ui`
(`Card`/`Column`/`Heading`/`Text`/`DataTable`) que le host peint ; dégradation
gracieuse en texte pour les clients sans support.

**Convention** : variantes **flagship `*_app`** (≠ remplacer les tools JSON), où
un visuel aide vraiment l'utilisateur. Les tools JSON équivalents restent la voie
par défaut/agent (« si le rendu échoue, utiliser le tool JSON équivalent »).
L'import de `prefab_ui` est **optionnel et guardé** dans le module (si l'extra
manque, les `*_app` ne s'enregistrent pas, les tools JSON restent). Premier jeu :
`tools/foncier.py` → `foncier_site_app` (fiche site : géocodage + parcelle +
bâti), `foncier_comparables_app` (ventes comparables DVF autour d'une adresse),
`foncier_prix_m2_app` (stats €/m² d'une commune). Mêmes clients open-data que les
tools JSON ; rendu **défensif** (colonnes dérivées des clés réelles) pour ne pas
dépendre d'un nom de champ. Gatés par le connecteur (namespace `foncier`).

Depuis, deux apps **spine** (hors gate) : `data_app` (datastore — table + fiche v2
schema-aware, `tools/datastore.py`) et `oto_doc_app` (pages/docs + KB, lecture
seule, `tools/docs_app.py`). ⚠️ Gotcha récurrent : **pas d'annotation de retour
`-> Card`** sur un tool `app=True` (hints résolus contre les globals du module au
build du schéma, or l'import prefab_ui est local à `register()` → NameError fatal
au boot, vécu #69). **Doc consommable par les agents = guide plateforme `mcp-apps`**
(servi par `oto_guide`, inventaire + quand app vs JSON + replis) — à tenir à jour
quand une app s'ajoute. ⚠️ **Guides = tout-DB (2026-07-16)** : la table `guides` est
la source de vérité des TROIS scopes on-demand (platform/org/user) ; les fichiers
`oto_mcp/guides/*.md` ne sont que des **seeds de boot** (`seed_platform_guides`,
idempotent, n'écrase jamais une ligne DB). Écriture platform = platform_admin
(MCP `oto_guide op=write scope=platform` / REST `PUT /api/me/guides/platform/{slug}`
/ dashboard `/platform/instructions`). Une édition durable doit AUSSI retoucher le
fichier seed (sinon un environnement neuf naît avec l'ancien texte). **Surface = UNE
capacité `me.guide`** (`capabilities/guides.py`, ADR 0042 §Convergence des surfaces,
2026-07-28) : `oto_guide` op-aware côté MCP + `me.guides.*` côté REST, **mêmes
handlers, une seule autz de scope** (`_owner_for_write`) — l'ex-`tools/guide.py`
(qui redéclarait la sienne) est supprimé. `scope` omis à l'écriture = `user`. Le cap
64 KB et le refus d'un corps vide s'appliquent désormais **aux deux faces**.

## Veille protocole MCP — suivre les SEP en amont (acté 2026-07-30)

**Règle : on suit les SEP, pas les specs.** Une spec publiée est un fait accompli ;
un SEP en discussion est une décision qu'on peut anticiper (voire influencer).
Domicile : PR markdown dans `seps/` du repo `modelcontextprotocol/modelcontextprotocol`
(SEP-1850 : numérotation dérivée de la PR, statut porté par les labels, sponsor
identifié). Revue périodique des SEP `proposed`/`accepted` qui touchent **ce qu'on
utilise** — transport streamable HTTP, autorisation/OAuth, tools, MCP Apps (SEP-1865),
tasks. On ignore ce qu'on n'a jamais adopté (sampling, roots, logging : dépréciés
le 2026-07-28, zéro occurrence ici).

**D'où ça vient** : la spec `2026-07-28` rend MCP stateless (SEP-2575 : plus
d'`initialize`, plus de `Mcp-Session-Id`, contexte par appel dans `_meta` ; SEP-2567 :
l'état cross-appel passe par des handles en arguments d'outil). C'est **exactement**
l'ADR 0038, qu'on a tranchée en avance — mais empiriquement, en encaissant le bug en
prod (claude.ai frappe un `Mcp-Session-Id` neuf à chaque appel, cf. `call_axes.py`),
pendant que les SEP concernés étaient publics. Bon pari, mauvaise méthode.

**À traiter d'ici la migration** (bloquée tant que FastMCP ne porte pas `2026-07-28`,
plancher actuel 3.4.2) : ① `ttlMs` + `cacheScope: **private**` obligatoires sur
`tools/list` (notre liste varie par identité, ADR 0015/0031 — un intermédiaire ne doit
jamais la partager) ; ② suppression de la résumabilité SSE ⟹ un stream cassé se rejoue
en requête neuve : les outils longs (browser, INPI, fullenrich) doivent être
**idempotents** ; ③ DCR déprécié au profit des Client ID Metadata Documents — notre
façade DCR (`oauth_facade.py`, palliatif du Logto self-hosted sans DCR) a une date de
péremption → épic sécurité auth/MCP #35 ; ④ MRTR (`resultType: "input_required"`)
remplace elicitation/sampling : **pas une dette** ici (nos `*_connect_start` /
`*_connect_status` sont déjà des handles), une standardisation possible.

## Conventions

- **Jetons de contexte d'appel = noms RÉSERVÉS, préfixés `_`** (ADR 0038 amendée 29/07,
  oto-backend#250) : `_org`, `_project`, `_group`, `_account`, `_instance`, `_run_id`
  (`call_axes.py`). Ils sont advertisés sélectivement au schéma des tools concernés, lus
  des args bruts, posés en ContextVar, puis **retirés avant le dispatch**. Le préfixe est
  ce qui rend ce retrait sûr : un tool peut déclarer `account`/`org`/`project` en argument
  MÉTIER sans risque. Tant qu'ils portaient les noms NUS, le retrait mangeait de vrais
  arguments **en silence** — `oto_use_org(org=)` (l'org cible, 04/07) puis
  `aiark_company_search(account=)` (le filtre société, 28/07 : AI Ark renvoyait sa base
  entière, 72M lignes, sans la moindre erreur). Ne JAMAIS nommer un argument de tool
  `_<quelque chose>` (tripwire `test_call_axes_business_param_collision.py`). ⚠️ La prose
  du bloc A prescrit ces jetons : elle vit dans `instructions.py` **ET** en DB
  (`platform_instructions['secret_sauce']`) — mettre les deux à jour, la DB **après** le
  déploiement prod, sinon l'agent reçoit une consigne que le serveur ne sait pas honorer.
- Nouveau connecteur = (1) un fichier `tools/<service>.py` exposant `register(mcp)`,
  (2) une **entrée au registre `providers.py`**. `register_all` (`tools/__init__.py`)
  **DÉRIVE le chargement du registre** (#24, fin de la liste hardcodée) : il boucle
  sur les providers `kind="tools"` et importe `Connector.modules` (défaut = nom du
  provider ; renseigner `modules` si module ≠ nom, ou plusieurs modules par provider —
  ex. `sirene`→`fr`, `google`→`gmail`/`datastore`/`tasks`). Chaque import en
  try/except (un connecteur cassé ne fait pas tomber le serveur). `meta`/`orgs`
  (spine) + `remote`/`mount` (génériques) restent chargés explicitement. ⚠️ Le
  namespace déclaré doit matcher `namespace_of(tool)` (1er token avant `_`) — pas de
  namespace multi-mot (`culture_spectacle`→`culture`), sinon fail-open du gate.
  Le garde-fou `test_tools_module_derivation_matches_filesystem` (`tests/test_capabilities_drift.py`)
  est **auto-maintenu** (croise `tools/*.py` au registre) — ajouter un connecteur
  (fichier + entrée registre) le garde vert SANS rien y toucher ; il casse seulement
  sur un **fichier orphelin** (connecteur posé mais pas déclaré → dort invisible) ou un
  **module fantôme** (faute dans `modules=`/nom). Seul un **module spine** chargé
  explicitement (rare) s'ajoute à `_EXPLICIT_TOOL_MODULES`. Le job `test` tourne
  **sur les PR ET sur push main** (`deploy-canari.yml` « Deploy preprod », `on:
  pull_request` + `push` sur main ; required check de branch protection sur main) et
  au **tag** (`deploy.yml` « Deploy prod »), et installe oto-core **au tag épinglé**
  (runner neuf → pin du pyproject) : un test rouge bloque le merge ET le deploy (les
  deux jobs `deploy` ont `needs: test`). Garde-fou anti-version-skew : `test_tools_client_methods_exist.py`
  vérifie STATIQUEMENT que les méthodes appelées sur le client existent sur la classe
  oto-core épinglée (un tool en avance de phase sur son oto-core casse la PR au lieu
  d'atteindre la prod — leçon `folk_get_user`). Portée élargie le **31/07** : `_client()`
  annoté `-> tuple[Classe, …]` compte comme `-> Classe`, et les variables qui REÇOIVENT
  le client (`client, _ = _client()`) sont suivies — `tools/apollo.py` cumulait les deux
  et sortait ENTIÈREMENT de la couverture, en silence. Seuls les attributs **appelés**
  comptent (un client à sous-objets — `client.companies.list()`, Attio — porte ses
  namespaces en attributs d'instance : les compter produirait un faux positif, et un
  garde-fou qui crie à tort finit ignoré). Un module avec un `_client()` hors portée fait
  désormais échouer `test_no_module_silently_uncovered`, sauf s'il est déclaré dans l'une
  des deux catégories nommées (sous-objets ; **dispatch dynamique** `getattr(client, m)()`
  — serper, serpapi, brightdata, cloro, spott, statiquement invérifiables et donc à
  découvert, ce qui est assumé et visible plutôt qu'implicite).
- **Ordre des middlewares MCP = contrat, pas un détail (02/08).** fastmcp exécute
  `instance.middleware` dans l'**ordre d'ajout** : le PREMIER ajouté est le plus
  **EXTERNE** (`_run_middleware` wrap en `reversed()`, vérifié empiriquement). Deux
  commentaires historiques croyaient l'inverse (« ajouté en dernier pour envelopper ») →
  `CallContextMiddleware` et `FieldRedactionMiddleware` tournaient au plus INTERNE, donc
  la ContextVar `_CALL_ORG` d'un appel épinglé `_org=` était **reset avant** que la
  rédaction de champs et le calllog (plus externes) ne relisent `current_org` : politique
  de rédaction et `org_id` d'audit de l'org **maison**, pas de celle de l'appel. Invisible
  quand les deux coïncident (le cas courant), faux sinon. Ordre correct (extern→interne) :
  `CallContext` → `FieldRedaction` → `ErrorEnvelope` → `UserDisabledTools` →
  `DynamicInstructions` → `ToolCallLogger` → `Sentry` (innermost : traceback brut au plus
  près du handler, et son `event_id` est posé AVANT que le calllog n'écrive la ligne).
  Figé par `tests/test_middleware_order.py` — le changer demande de relire ses invariants.
- **PERF — le serveur est MONO-LOOP : aucun I/O bloquant dans la boucle.** Un handler
  de tool qui n'`await` rien doit être `def` sync (threadpool) ; du DB sync dans un
  middleware = même règle (`run_in_threadpool`). Deux modes de gel vécus + garde-fous
  CI (`test_no_blocking_async_handlers`), pool borné (`timeout=5`), observabilité
  (loop_watch/aiodebug, py-spy box, Kuma timeout 30s).
  **Détail (incidents, recettes de diagnostic) : `docs/event-loop-perf.md`**.
- **Cran d'activation (ADR 0010/0011)** : déclarer un connecteur ne l'expose PAS —
  gate DB `connector_activation.py` (master global ± override org, deny-by-default).
  Gate à la **VISIBILITÉ par session** (`UserDisabledToolsMiddleware` + `connector_
  activation`, **fail-open**) : `register_all` charge tout inconditionnellement, le
  middleware masque les tools d'un connecteur non activé pour l'org → (dés)activer
  prend effet à la session suivante **sans restart**, override par org OK. Filtre
  aussi `/api/connectors` (catalogue) ; overlays catalogue `family` (dérivée) +
  `category` (curée) + `publisher` (curé, `_PUBLISHER_BY_CONNECTOR`) + `logo_url`
  (dérivé du **CDN logo.dev** par `Connector.logo_url_for` : domaine de marque curé
  `_LOGO_DOMAIN_BY_CONNECTOR` + token publishable `LOGODEV_TOKEN` en env ; pas de S3,
  pas de seed. L'absence est DÉCLARÉE dans `_SANS_LOGO_DE_MARQUE` (générique/maison :
  monogramme côté UI) + tripwire — sinon un oubli se confond avec un choix).
  Surface admin `/api/admin/connectors/activation`
  (`api_routes_connectors.py`) + écran dashboard « connector activation ».
- **Connecteur client-sensible = JAMAIS de code ici** : pont via le connecteur
  **`http` générique** (ADR 0037, amende 0034/0003/0011). Le connecteur historique
  **`bridge`** (`kind="remote"`, tools `bridge_describe`/`bridge_call`,
  `tools/remote.py`) a été **RETIRÉ le 2026-07-16** (oto-backend#108) : un bridge
  n'est qu'une **API HTTP** que le service distant re-expose → l'org configure sur
  la carte `http` son `base_url` (endpoint du bridge) + `auth_mode=bearer` + `token`
  M2M (`credential_fields`, jamais dans le namespace → catalogue sans nom client),
  et l'agent appelle `http_get`/`http_post`. Le service distant détient le credential
  métier (contrat ADR 0003 §4 : bearer M2M, politique bornée côté bridge, audit
  `X-Oto-Sub`). Visibilité = régime commun (activation × sélection 0019/0050 — hors
  socle, installable). Pilote : le **bridge back-office Movinmotion** (repo privé),
  migré `bridge`→`http` le 2026-07-16 (credential au groupe finance, réseau VPC
  privé). Le concept « remote data-driven » (base_url sur un provider hors registre)
  subsiste dans `org_secret_meta`, mais **sans entrée de catalogue** `kind="remote"`.
- **Tool API-keyé = déclarer le connecteur dans le registre `connectors.py`**
  (avec `keyed=True` + `auth_modes`) — `KEY_PROVIDERS` et tout le reste en
  dérivent. Le coffre `connector_credentials` est générique (pas de colonne
  par provider) : aucune migration de schéma à ajouter. Sinon `resolve_api_key`
  lève `Unknown provider` à l'appel. Puis poser la clé plateforme en DB via
  `oto_admin_set_platform_key` (plus de bootstrap SOPS — le provider sans clé
  DB n'a simplement pas de mode plateforme).
- **Credential = champs déclarés (modèle générique multi-champs, ADR 0011)** : un
  provider porte `credential_fields` (`CredentialField` name/label/secret/reveal) ou
  les dérive de `secret_kind` (`api_key`=1 champ, `basic_auth`=2). Le coffre encode
  les champs dans l'unique `secret_enc` via `credentials_store.pack_secret`/
  `unpack_secret` (3 formats : valeur brute 1 champ / base64 `email:password` /
  json ≥2). L'endpoint `/api/settings/api-keys/{provider}`, le formulaire dashboard
  et `status_for` bouclent sur `secret_fields` — **zéro branche par connecteur** ;
  un nouveau connecteur multi-secrets = une déclaration. Résolution : `resolve_api_key`
  (1 clé keyed + platform/quota) **ou** `resolve_credential_fields` (byo multi-champs
  sans quota, ex. `silae` : client_id/client_secret/subscription_key). `cookie`/`oauth`
  (linkedin/google) ont des flux dédiés → `secret_fields` vide.
- **Sonde « tester la connexion » par connecteur** (`connector_verify.py`, registre
  calqué sur `browser_session.register`) : un connecteur enregistre une `_verify(fields)`
  qui **lève sur échec** (le message d'exception = le retour d'erreur). Capacité unique
  `connectors.verify` (MCP `oto_instance(op="verify")` — console ADR 0047 + REST `POST /api/me/connectors/{provider}/verify`,
  `authz=ORG_MEMBER`, `level` auto|org) → `{ok, error, elapsed_ms, level, ref}`, jamais un 500 ;
  `level`/`ref` (ex. `org:2:salesforce`) DÉRIVÉS de la même entité, sinon un `ok` sous
  `auto` ne dit pas quel cran de la cascade a répondu. `run()` transporte aussi
  `instance=(entity_type, entity_id, account)` aux sondes qui le DÉCLARENT — vital dès
  qu'une sonde a un effet de bord (rotation : cf. `docs/connector-vault.md`) ;
  `providers.public_catalog` expose `verifiable: connector_verify.supports(name)` (front
  gate le bouton). **Une bonne sonde teste l'auth ET les scopes**, pas juste l'auth :
  seed Zoho (`tools/zoho.py::_verify`) fait un refresh OAuth brut (valide client/secret/
  refresh/région d'un coup + capte le `scope` accordé) PUIS une **lecture réelle**
  (`ZohoClient.list_records` sur Contacts/Deals/Accounts/Leads, `per_page=1`) — une clé
  qui authentifie mais n'a **aucun scope CRM** (ex. clé Zoho **Analytics** posée par erreur
  sur le connecteur CRM) est rejetée avec le scope réel dans le message. ⚠️ Gotchas Zoho
  empiriques : le refresh renvoie **HTTP 200 + body `{"error":"invalid_client"}`** (région/
  client faux) ou `invalid_code`/`invalid_grant` (refresh mort) ; l'API CRM **v7 exige un
  param `fields`** (une lecture nue → 400, pas un scope-mismatch) → sonder via `list_records`
  (qui fournit les `DEFAULT_FIELDS`), pas un `GET /crm/v7/{module}` brut.
- Docstrings = contrat LLM (le modèle choisit les tools là-dessus). Précis, pas verbeux.
- **Doc how-to d'un connecteur = un markdown**, `oto_mcp/connector_docs/<nom>.md`
  (nommé comme son module), sections `## <kind> — <titre>`, servie au catalogue et à
  toutes les fiches. Une URL de rappel ne s'y écrit JAMAIS en dur — marqueur
  `{{callback:/chemin}}` résolu à la lecture, car elle diffère prod/preprod (tripwire).
  C'était un dict de 850 lignes de chaînes Python : la prose y devenait intouchable, et
  la fiche Salesforce a fini par décrire un modèle d'app que Salesforce avait désactivé.
- **Aucune résolution de secret côté serveur hors DB/env de process** : pas de
  `get_secret`/`require_secret` oto.config dans le code serveur (l'unit pose
  `OTO_CONFIG_DISABLE_SOPS=1`, tout résidu échoue fort).
- LinkedIn nécessite le **vrai Google Chrome système** (`google-chrome-stable`, apt)
  sur l'host — PAS le Chromium bundlé Patchright (empreinte TLS ≠ Chrome de bureau
  → bloqué par LinkedIn). `_require_chrome_channel` (`tools/linkedin.py`) force
  `channel="chrome"` et lève une erreur si absent.
- WhatsApp/Telegram/Instagram = messagerie **Unipile** (cf. §WhatsApp) — aucune dép
  Node côté backend. Le Baileys Node (`oto-core/.../whatsapp/node/`) ne sert plus
  qu'à la CLI `oto whatsapp` (fallback archivé).
- Attio (`tools/attio.py`) expose CRUD complet : records (companies/people/deals),
  notes (sauf update body, limite API), tasks, lists, entries, workspace_members,
  comments, threads, meetings, call_recordings + meta (objects, attributes). Pas
  de quota plateforme — chaque user pose sa clé sur `/account`. **Gotcha** :
  `attio_list_threads` renvoie 400 sans `parent_object`/`parent_record_id` —
  toujours filtrer par parent.

## Commands

```bash
# Transport stdio RETIRÉ (2026-06-13) : oto-mcp ne se sert qu'en streamable_http
# (toujours authentifié Logto). Usage local = CLI `oto`. Pour un serveur local,
# lancer en http avec les LOGTO_* et taper avec un bearer.

# Tests — le venv .venv N'A PAS pytest (extra `dev` non installé) et `uv run pytest`
# crée un env éphémère SANS les deps projet (piège, ModuleNotFoundError). Recette :
uv pip install --python .venv/bin/python "pytest>=8.0" "pytest-asyncio>=0.24"
.venv/bin/python -m pytest -q

# Tester un CLONE (clone scratchpad, ou `git archive <commit>` pour isoler un commit du WIP
# voisin du tree partagé) SANS réinstaller les deps : réutiliser le venv local (deps+pytest
# présents) en résolvant `oto_mcp` depuis le clone.
#   cd <clone> && PYTHONPATH=<clone> OTO_CONFIG_DISABLE_SOPS=1 \
#     /data/oto/backend/.venv/bin/python -m pytest -q tests/...
#
# ⚠️ **Le `cd <clone>` n'est PAS cosmétique : c'est LUI qui fait marcher la recette.**
# `PYTHONPATH` seul NE PRIME PAS sur l'editable install — son finder vit dans `sys.meta_path`,
# consulté AVANT `sys.path`. Lancé depuis `/data/oto/backend`, le même PYTHONPATH importe
# donc `/data/oto/backend/oto_mcp` : on croit tester le clone, on teste le tree partagé.
# Le mode d'échec est un **faux négatif silencieux** — vécu 11/08, un agent a conclu « le
# code d'avant passe déjà mes 16 tests » en testant en réalité son propre correctif, ce qui
# invalide la seule chose que le clone servait à prouver.
# **Valider l'instrument avant d'en tirer une conclusion**, une ligne suffit :
#   cd <clone> && PYTHONPATH=<clone> /data/oto/backend/.venv/bin/python -c \
#     "import oto_mcp.db.search as m; print(m.__file__)"   # doit pointer DANS le clone
# Convention : tester la LOGIQUE PURE (helpers hors DB, ex. `effective_for_group`,
# `_connector_blocked`/seams) + les gardes de capacité par stub ; le chemin SQL est vérifié
# au déploiement (le job `test` du CI tourne le vrai suite avec toutes les deps).

# Deploy — modèle tronc unique (refonte 2026-07-20, ADR 0020) :
#   push `main`  → PREPROD (« Deploy preprod », deploy-canari.yml, script serveur
#                  oto-backend-canari.sh : git reset --hard origin/main → preprod)
#   tag  `v*`    → PROD    (« Deploy prod », deploy.yml, script serveur
#                  oto-backend.sh <tag> : git reset --hard <tag> → prod)
# Le deploy (les deux) = SSH box dédiée via runner self-hosted : reset au ref +
# pip install -e . + **force-reinstall oto-core depuis le tag pinné** (lu du
# pyproject ; pip saute sinon une dép VCS déjà présente) + restart + **smoke HTTP**
# (GET 200 /.well-known/oauth-authorization-server) + **rollback auto** si
# install/restart/smoke échoue. Le restart relance start-encrypted (refetch master
# key). ⚠️ start-encrypted.sh untracked → survit au git reset.
#
# Preprod = travailler sur `main`, commit, push : deploy preprod auto (gate
# `needs: test`). Claude Code (web) ouvre ses PR sur main → merge = deploy preprod.
git push origin main            # → PREPROD

# Prod = acte explicite : taguer un commit de main + pousser le tag.
git tag v1.2.3 && git push origin v1.2.3   # → PROD (tags v* immuables, ruleset)
# ⚠️ `canari` est DÉPRÉCIÉE (ne déploie plus) : un checkout encore dessus doit
# passer sur main (`git checkout main`). guard-main + sync-main-to-canari retirés.

# Logs
ssh -i ~/.ssh/alexis root@<box> "journalctl -u oto-mcp -f"

# DB inspect (PG managed) — depuis la box (env du process inclut DATABASE_URL via .env)
# ⚠️ `psql` n'est PAS installé sur la box dédiée → passer par le venv + psycopg :
ssh -i ~/.ssh/alexis root@<box> 'cd /opt/oto-mcp && set -a; . .env; set +a; ./.venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ[\"DATABASE_URL\"]) as c:
    for r in c.execute(\"SELECT sub, email, role FROM users\"): print(r)
"'

# ⚠️ Un script HORS SERVEUR ne voit AUCUN outil : `tool_registry.boot_tool_names()`
# rend [] tant que le registre n'est pas réchauffé (le serveur le fait au lifespan).
# Toute validation de nom d'outil renvoie alors un `unknown_tool` TROMPEUR — vécu
# 05/08, j'ai failli annoncer un blocage inexistant. Diagnostic fidèle :
#   register_all(mcp := FastMCP("x")); tool_registry.bind(mcp)
#   asyncio.run(tool_registry.warm_registry(mcp))   # → 665 outils, la validation passe

# ⚠️ Déchiffrer un credential ad-hoc : OTO_MCP_MASTER_KEY n'est PAS dans .env
# (fetchée au boot depuis Secret Manager) → recette complète + pièges (RuntimeError
# ≠ InvalidTag ; status_for = credential_status, jamais get_credential_with_meta) :
# docs/connector-vault.md §Déchiffrer un credential ad-hoc.
```

## Infra

Déployé sur une **box Scaleway dédiée** (ADR 0002, depuis 2026-06-11) : oto-backend isolé + Caddy + chiffrement du coffre actif, sert `mcp.oto.ninja`. **DB** = PostgreSQL managé partagé (`otomata-main`, DB `oto_mcp`). Le coffre `connector_credentials` est chiffré au repos (AES-256-GCM, master key en Secret Manager fetchée au boot, 0 plaintext). Object Storage S3 pour avatars/logos (`media_store.py`).

> **Détails machine = repo privé `otomata-tech/infra`** (IPs, IDs de secrets/zone/instance, systemd, runbook deploy, env de process) — pas ici (ce repo est public). Voir `infra/docs/oto-platform-state.md` + docs ciblés (`scaleway-managed-db.md`, `caddy.md`, `cloudflare.md`, `deploy-keys.md`). Toute intervention prod = skill `prod-init`.

> ⚠️ **PROD et PREPROD partagent la MÊME base** (constaté 07/08 : DSN **identiques** — même
> hôte, même DB — entre `/opt/oto-mcp/.env` et `/opt/oto-mcp-canari/.env`). Le « DB découplée »
> du bloc CUTOVER plus haut ne décrit **pas** l'état réel. Deux conséquences pratiques : une
> donnée écrite depuis la preprod est **la donnée de prod** (pas un bac à sable) ; et toute
> config portée par une COLONNE ne peut avoir qu'**une** valeur pour les deux environnements
> — ce qui exclut de distinguer prod/preprod par la base (vécu sur `orgs.front_base_url`, où
> la preprod émet donc des liens vers le front de prod). Vérifier avant de raisonner dessus :
> comparer les DSN par hash, jamais en les lisant en clair.

## Docs

- `docs/connector-model.md` — **carte d'ensemble** : les **3 couches** d'un connecteur (disponibilité / authentification / option de connecteur), la matrice des niveaux (user/groupe/org/plateforme), le vocabulaire canonique, le seam `access.has_option`. **À lire en premier** avant de toucher activation/clés/options (les autres docs ci-dessous = le détail par couche).
- `docs/connector-vault.md` — **archi centrale** : registre source unique (`connectors.py`), coffre chiffré unique `connector_credentials` (clés API + platform_keys + sessions linkedin/crunchbase/google multi-compte), enveloppe AES-256-GCM **obligatoire** (pas de plaintext), résolution + palier org, **credentials qui se consomment à l'usage (rotation)** et le modèle application-d'org ≠ jeton-d'identité. À lire avant de toucher credentials/registre/résolution.
- `docs/roles-and-resolution.md` — rôles (3 paliers) + cascade de résolution de clé / grants / platform keys.
- `docs/doctrines.md` — doctrine & skills d'org (`oto_procedure`, versionnée) + **renommer un outil = migrer les procédures** (refs `<tool:slug>` en DB, angle mort du CI).
- `docs/auth-logto.md` — auth Logto ES384, discovery RFC 9728, façade DCR.
- `docs/rest-api.md` — inventaire des endpoints REST `/api/*`.
- `docs/federation.md` — fédération MCP : mount (per-user) vs remote/bridge (org).
- `docs/usage-loop.md` — boucle d'usage ADR 0017 (calllog + feedback + déroulés).
- `docs/monitoring.md` — monitoring des appels MCP (tool_call_log + surface admin).
- `docs/datastore.md` — datastore spine PG (`data_*`) + OAuth Google per-user (setup GCP, scopes).
- `docs/groups-and-roles.md` — groupes/départements & hiérarchie de droits (ADR 0012).
- `docs/browser-automation.md` — substrat Browserbase (Context/Live View/run_fetch), connecteurs brevo/crunchbase/pennylaneged, connecteur générique `browser` (N sites derrière login), LinkedIn isolation de session.
- `docs/projects.md` — projet (liens typés, docs), livraison client cascade, endpoint MCP + partage navigable par projet (`<slug>.{mcp,share}.oto.cx`).
- `docs/unipile.md` — messagerie hébergée : mode plateforme, DSN, sélecteur d'identité, comptes partagés (#55).
- `docs/ownership.md` — primitive de ressource possédée (can_access/can_govern, tripwire owner_pairs, abolition du perso).
- `docs/email.md` — envoi per-org par connecteur (scaleway BYO TEM + resend), différé/quiet hours.
- `docs/event-loop-perf.md` — les 2 modes de gel mono-loop + protections + recettes py-spy/aiodebug.
- `docs/redaction.md` — **rédaction de champs** : middleware unique (FieldRedactionMiddleware), rien par défaut + templates 1-clic, **schéma OBSERVÉ** (capture passive `connector_schemas` — passthrough d'API tierces → on observe au lieu de déclarer), dry-run preview, moteur `FieldFilter` (oto-core).
- `docs/live-migrations.md` — **migrations vivantes sur la DB partagée canari/prod** : la danse en N lots promus, copies `to_regclass` newer-wins, bascule d'arbitre ON CONFLICT avant drop de PK, PK nommées, pièges (fail-open des gates, `gh pr merge` avant le guard, absorption de WIP).
