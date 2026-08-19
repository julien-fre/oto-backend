---
title: Conventions du backend
type: reference
description: >-
  Les règles de travail du backend, chacune née d'un incident daté : ce qu'un test doit
  décrire (le système, pas l'intention), le montage réel comme seul banc d'un garde-fou,
  l'interdiction d'écrire une adresse en dur, les jetons de contexte d'appel réservés,
  le budget de ce qu'un outil renvoie, l'ordre des middlewares MCP, la contrainte
  MONO-LOOP (aucun I/O bloquant), et le cycle d'un connecteur (cran d'activation,
  registre `connectors.py`, credential multi-champs, sonde de connexion, doc how-to,
  aucune résolution de secret hors DB/env). À lire avant d'écrire du code ici, et avant
  d'ajouter un garde-fou ou un connecteur.
---

# Conventions du backend

Extrait de `CLAUDE.md` le 2026-08-19 : 281 lignes qui pesaient le quart de la carte.
Le contenu n'a pas changé — seule sa place a bougé.

- **Un test qui affirme une INTENTION grave le bug.** Trois fois le 13/08 : des tests
  vérifiaient que la découverte annonçait l'émetteur du tenant, que le lien collait notre
  chemin sous leur domaine, que l'adresse valait `dashboard.oto.ninja` — tous verts, tous
  protégeant un défaut qui a cassé la prod ou servi la preprod à un client. Un test doit
  décrire le SYSTÈME (le document servi, la route montée, la dérivation), pas la valeur
  qu'on croit juste. Corollaire : **une chaîne de découverte d'auth se prouve avec un vrai
  client MCP avant la prod**, jamais avec des assertions sur un document.
- **Une adresse rendue à l'utilisateur ne s'écrit jamais en dur** (`config.dashboard_url`,
  tripwire `test_dashboard_url_par_tenant.py`). Trois variables ont coexisté pour la même
  adresse et la prod n'en posait qu'une : tout ce qui lisait les autres servait la
  **preprod**, y compris à un client. Le défaut vise désormais la prod — un environnement
  mal configuré doit dégrader vers le vrai produit, pas vers un bac à sable.

- **Un garde-fou d'inventaire s'exerce sur le MONTAGE RÉEL, jamais sur une fixture
  partielle.** Trois cas en deux jours (11-12/08) où le banc du garde-fou divergeait du
  réel et le garde-fou **mentait par omission** : le glob anti-routes-manuelles voyait
  45 chemins sur 81 (`api_routes_*.py` rate `api_routes.py`) ; `openapi.build()` local
  rend 138 opérations quand le document SERVI en porte 233 (les routes main n'existent
  que servies — **auditer le document servi, jamais le build**) ; l'inventaire des flux
  de connexion montait les tools sans les routes REST, or au boot réel ce sont les
  routes qui importent les modules d'auth. Racine commune : la fixture reproduit une
  PARTIE du démarrage et le test promet le TOUT.
  **Même racine côté DONNÉES (3 cas sur le seul lot M4, 13/08)** : un banc qui
  RECONSTITUE le schéma mesure la représentation qu'on s'en fait, pas le système —
  la table sans son vrai DDL (un `id BIGSERIAL` supposé, inexistant : clé composite),
  `nodes` sans ses deux GIN (99 % du coût d'écriture au banc M0), un peuplement
  uniforme là où la prod est un vivier (la fausse absence de dégradation de la file :
  la table s'épuisait avant que la dégradation n'apparaisse). Et toujours dans le sens
  RASSURANT. Règle : un banc s'exerce sur le VRAI DDL (extrait de `_schema.py`) et une
  population de forme réelle — sinon il rend des verdicts, pas des mesures. Un 4e cas le soir même, dans l'AUTRE
  sens : un test qui importait lui-même le module qu'il inventoriait certifiait une
  couverture inexistante — le flux n'était déclaré nulle part au boot réel, seul le test
  le chargeait (v1.88.0, corrigé v1.88.1). Règle complète : **le banc d'un garde-fou
  d'inventaire charge ce que charge le boot, ni plus NI MOINS** (le vrai boot
  `register_all` + routes, ou le document servi) — et à sa création, **prouver qu'il
  mord** en lui présentant l'anomalie qu'il prétend attraper (retirer la déclaration ⟹
  l'inventaire doit tomber).
  Corollaire sécurité (13/08) : **un signalement de vuln se vérifie en CONSTRUISANT
  l'attaque**, jamais en jugeant les bibliothèques — le XXE signalé sur l'extraction ne
  s'appliquait pas (entités non résolues, prouvé par l'exploit), mais le construire a
  révélé le voisin réel : la bombe de décompression (400 ko → 638 Mo de RSS, mono-loop
  = tout le serveur). La garde se pose sur le CATALOGUE du zip (les tailles annoncées,
  sans décompresser — le contrôle ne peut pas être victime de ce qu'il contrôle), et on
  s'arrête PENDANT la lecture — jamais accumuler-puis-tronquer.
  Variante « chemin jamais emprunté » (13/08 soir) : **la suite ne couvre pas une clause
  de rattrapage que rien n'exerce** — une clause `except` ne s'évalue qu'à la propagation
  (un nom non importé y dort sans erreur : boot vert, suite verte, NameError en prod au
  premier doublon — trouvé par revue adversariale post-découpage, pas par les tests).
  Deux parades posées : le test du CHEMIN DE RATTRAPAGE lui-même (provoquer l'exception,
  vérifier le contrat de l'appelant qui en dépend), et après toute scission de module un
  balayage des noms lus sans être importés ni définis (test grossier niveau module,
  suffisant pour le nom hérité d'un fichier scindé).
- **Tree partagé entre sessions : deux sessions ne partagent JAMAIS un fichier — le
  séquencement prime, le staging n'est qu'un filet.** Vécu 13/08 (main rouge) : un
  `git add <chemin>` EXPLICITE a absorbé ~148 lignes du WIP d'une session voisine dans
  un commit poussé — le chemin explicite ne protège que du FICHIER voisin, pas du
  **HUNK** voisin dans le même fichier ; le commit appelait une fonction restée dans
  le stash de l'autre session (AttributeError sur les chemins d'écriture, CI rouge).
  Règle : le superviseur séquence les fichiers contendus (un seul occupant à la fois) ;
  à défaut, staging au grain hunk ; et un commit dont le diff dépasse son périmètre
  annoncé ne se pousse pas. Corollaire (13/08 soir) : **un WIP qui ne compile pas n'est
  pas un WIP, c'est une panne pour tout le monde** (l'import du package échoue ⟹ plus
  aucun test ne tourne sur le tree, hotfix prod bloqué inclus) — découper en édits qui
  laissent chacun le module IMPORTABLE.
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
  du bloc A prescrit ces jetons : la source est `instructions.py` (le seed versionné).
  **PAS d'override DB (`platform_instructions['secret_sauce']`) sauf divergence
  DÉLIBÉRÉE** — un override qui recopie le seed est une MINE : il fige la prose au jour
  de sa pose et toute évolution du code cesse de se propager sans que rien ne le
  signale. Vécu 12-14/08 : la copie DB a survécu deux jours au retrait d'`abandoned`
  (#311) — le texte le plus lu de la plateforme prescrivait une valeur que
  `run_finish` REFUSAIT. Purgé le 14/08 (l'override est VIDE, le seed sert seul ;
  vider l'override = « rétablir le défaut » depuis v1.117.0). Si un jour on diverge
  pour de vrai : mettre les deux à jour, la DB **après** le déploiement prod — et
  savoir que cette règle repose sur la mémoire, pas sur un garde-fou.
- **Ce qu'un outil RENVOIE a un budget, et il se mesure — pas une consigne (14/08).** Sept
  signaux d'usage en six jours, tous le même défaut : un payload qu'un agent ne peut pas
  lire (`linkedin_aiark_search` 3 M caractères, `oto_doc op=list` 201 K, `linkedin_unipile_post
  op=feed` 67 K, `oto_project op=list` 73 K). Chaque fois, le client déverse en fichier puis
  reparse au `jq` — et **un agent sans shell (client MCP nu, n8n) cale tout court** : pour lui
  un tool trop verbeux n'est pas cher, il est inutilisable. Quatre règles en sortent :
  - **Une LISTE rend son index, jamais les corps.** Elle sert à choisir quoi ouvrir : de quoi
    adresser, trier, et écarter sans se tromper. Seam partagé `output_projection.summarize()`
    — les colonnes-corps deviennent `<champ>_length` et la réponse **NOMME** ce qu'elle a
    écarté (bloc `projection`). Le brut reste atteignable (`fields=["*"]`), un `fields=[]` est
    **refusé** plutôt qu'avalé. Fait sur `oto_doc`/`oto_project` ; `guides` et `org_instructions`
    le faisaient déjà.
  - **Projeter ≠ tronquer.** Retirer des colonnes est réversible et annoncé ; couper un texte à
    N caractères est une mutilation silencieuse — l'agent croit avoir lu. D'où la TAILLE, jamais
    un extrait (mesuré le 11/08 : un feed coupé à 600 c. tombait pile avant la chute qui
    départage un post de fond d'une pub, 2 cas limites sur 5 tranchés à l'aveugle).
  - **Denylist de clés nommées, jamais une allowlist** (leçon `fr_get`/`liste_idcc` : un champ
    oublié disparaît en silence). Le seam ne connaît aucun outil — chaque connecteur déclare
    ce qu'il coupe, là où il sait ce que ses champs valent (`full=True` rend le brut).
  - **Le handshake aussi a un budget.** Les 6 jetons `_*` sont recopiés dans ~400 schémas : une
    phrase écrite dans `call_axes.py` est payée 400 fois, à chaque tour, par chaque agent. Ils
    pesaient **48,2 % des 880 K caractères servis** par `tools/list` ; ramenés à 36,2 % en
    cessant de redire le bloc A (-41 400 tokens). Bornes gardées par `test_call_axes_budget.py`
    et `test_list_view_budget.py` — **rallonger devient un choix visible**, pas une dérive.
  ⚠️ **Aucune de ces tailles n'est instrumentée** : `tool_calls` n'a pas de colonne de taille de
  réponse, donc « quel connecteur rend le plus gros payload ? » reste sans réponse et le 8ᵉ cas
  sera découvert par l'utilisateur qui s'y cogne (oto-backend#340).
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
  CI, pool borné (`timeout=5`), observabilité
  (loop_watch/aiodebug, py-spy box, Kuma timeout 30s).
  ⚠️ **DEUX garde-fous, de natures différentes, parce qu'un middleware échappe au
  premier** : `test_no_blocking_async_handlers` lit le source des `@mcp.tool` (async
  sans `await` = rejeté) — or un middleware n'est pas un tool ET doit `await
  call_next`, donc il passe deux fois à côté ; `test_no_blocking_db_in_middleware`
  **observe le thread** qui emprunte une connexion (mouchard sur `db._conn._get_pool`)
  et refuse tout accès DB depuis la boucle. Gel de prod du 15/08 : le handshake
  composait l'artefact de session — la cascade de statut de TOUS les connecteurs —
  dans la boucle. Un chemin de la même classe reste à traiter, listé dans le doc.
  **Détail (incidents, recettes de diagnostic) : `docs/event-loop-perf.md`**.
- **Un 502 en rafale n'est pas forcément un gel** — deuxième cause, distincte (#352,
  nuit du 15-16/08) : un POST `/mcp` en vol quand la session streamable-http se termine
  laisse une réponse ASGI **incomplète** (le SDK MCP pousse dans un stream mort), uvicorn
  ferme le transport, et Caddy — qui tenait la connexion pour réutilisable — rend des 502
  sur elle **et sur les requêtes voisines de son pool keep-alive** (des `/api/*` de
  workers qui n'ont jamais parlé à `/mcp`). Le discriminant tient en un chiffre : ces
  502-là durent **~0,2 s** (le gel, lui, fait attendre). ⚠️ **Ce qui remonte à uvicorn
  n'est PAS `BrokenResourceError`** — le SDK l'attrape et la logue ; ce qui s'échappe est
  le `RuntimeError … after response already completed` de son 500 écrit par-dessus le 202
  (mesuré : 1433/1433). Chercher `BrokenResourceError` en haut de pile ne trouve rien.
  Garde : `client_disconnect_guard.py`,
  posée par `server.build_root_app` en couche la plus EXTERNE — elle complète la réponse
  à la place du client parti et n'attrape QUE `error_taxonomy._is_client_disconnect`
  (même prédicat que le drop Sentry, une seule source) ; toute autre exception traverse,
  figé par `tests/test_client_disconnect_guard.py`. ⚠️ **Rien à attendre d'un bump `mcp`** :
  le site fautif est identique de 1.27.2 à `main`/2.0.0, la PR upstream qui le garderait
  n'est pas mergée et le backport 1.x est refusé (`not_planned`).
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
