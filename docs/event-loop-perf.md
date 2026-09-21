---
title: Perf event-loop — le serveur est MONO-LOOP (les 5 modes de gel)
type: explanation
---

# Perf event-loop — le serveur est MONO-LOOP (les 5 modes de gel)

> ⚠️ Ce titre a dit « les 2 modes » jusqu'au 2026-08-27, « les 3 » jusqu'au
> 2026-09-01 et « les 4 » jusqu'au 2026-09-09, et c'était vrai à chaque écriture. Les
> deux premiers modes sont des **placements** d'I/O (un handler async sans await, puis
> un middleware) ; le n°3 est d'une autre nature (le placement est correct, la requête
> est lente) ; le n°4 est un placement de nouveau, mais à un endroit qu'aucun des trois
> garde-fous ne regardait — **le seam qui monte les capacités**, donc 285 handlers d'un
> coup ; le n°5 n'est pas un placement mais une **répétition** : une valeur juste, lue
> au bon endroit, redemandée dix fois par appel parce que personne ne la retient.

> Extrait du CLAUDE.md (refactor 2026-07-02) — domicile du détail ; le CLAUDE.md garde le résumé + pointeur.

**PERF — un handler de tool fait du I/O bloquant ⟹ il est `def` SYNC, jamais `async def`.**
  Le serveur est **mono-event-loop** (`uvicorn.run(app)`, pas de `workers=`). FastMCP route
  un `def` sync en **threadpool** (`call_sync_fn_in_threadpool`) mais exécute un `async def`
  **dans la boucle**. Nos connecteurs appellent des libs **synchrones** (`requests` via
  france_opendata, DuckDB, clients HTTP sync) → un `async def` **sans `await`** gèle TOUTE la
  boucle le temps de l'appel (vécu 2026-06-25 : `/health` à 110 s, p95 `fr_stock_search` 218 s ;
  fix `async`→`def` sur `fr.py`/`fr_stock.py` → `/health` ~0,1 s). Règle : un handler `tools/*.py`
  qui n'`await` rien doit être `def`. Ne garder `async def` que s'il `await` réellement (httpx
  async, etc.). NE PAS ajouter de workers uvicorn (état de session streamable_http en mémoire).
  **Lot connecteurs bouclé le 2026-06-29** (361 handlers convertis ; cause re-vue = un flot
  de `serper_scrape` gelant la boucle, `/.well-known` à 1,4–10,5 s sur une box à 0,2 de load).
  **CI-enforcé** : `tests/test_no_blocking_async_handlers.py` casse si un `@mcp.tool` async
  n'`await` rien dans son **propre scope** (AST own-scope, auto-maintenu, pas de whitelist) ;
  ⚠️ **la règle vise les HANDLERS, pas tout ce qui est async** : un *callback* awaité par
  FastMCP reste légitimement async — le cas d'école était le `client_factory` de la
  fédération MCP (`mount.factory`), partie le 2026-09-09 (ADR 0069). « Pas d'await » ne
  suffit donc pas à conclure : vérifier que c'est un handler, pas un callback. Bornes connexions PG posées au
  passage (`db._connect_options` : `idle_in_transaction_session_timeout` anti-zombie-lock).
  **2ᵉ mode de gel identifié + corrigé (2026-07-02, py-spy en flagrant délit)** : du DB
  sync dans un MIDDLEWARE de la loop (`_authenticate`, gate ViewAs) × un blip de la RDB
  (SSL eof) → `pool.getconn()` attendait 30s en gelant le serveur ENTIER (2 downs).
  Protections : `ConnectionPool(timeout=5)` (`OTO_MCP_DB_POOL_TIMEOUT`) + chemin d'auth
  en `run_in_threadpool`. Observabilité posée : `loop_watch.py` (aiodebug — tout callback
  bloquant ≥1s est nommé au journal, ≥10s → event Sentry), py-spy sur la box
  (`py-spy dump --pid $(systemctl show -p MainPID --value oto-mcp)` PENDANT un gel),
  moniteur Kuma timeout 30s (timeout=0 = aveugle aux gels). RDB upgradée pico→nano.

## Le mode n°1 a lui aussi une porte dérobée : le handler qui `await`… plus bas

Le garde-fou AST `test_no_blocking_async_handlers` demande « ce handler `async def`
`await`-t-il quelque chose dans son propre scope ? ». Un handler qui fait du I/O
**synchrone d'abord** et n'`await` qu'**ensuite** répond oui — et gèle quand même la
boucle pendant tout le début.

Cas vécu, trouvé le 28/08 en instruisant le signal #491 : `web_read` (`tools/web.py`)
n'`await` que son cran ③ (le navigateur hébergé, opt-in), tout en bas ; ses crans ①
(`requests`) et ② (`SerperClient`, qui s'auto-limite par un `time.sleep`) sont
entièrement synchrones et tournaient dans la boucle. Journal de prod du 17/08 : **371
lectures, 11 au-delà de 30 s, une à 57,5 s** — autant de boucle tenue pour tous les
utilisateurs à la fois, sans que le garde-fou ait rien à redire.

**Deux leçons, et la seconde est la plus chère :**
- **« Le handler await » ne veut pas dire « le handler ne bloque pas ».** Le critère
  utile est *où* se trouve le premier await par rapport au I/O — ce qu'un AST
  own-scope ne peut pas décider. D'où, comme pour les middlewares, un test qui
  **observe le thread** plutôt que le source (`test_les_crans_bloquants_ne_tournent_pas_dans_la_boucle`).
- **Un timeout par socket n'est PAS un timeout de requête.** `_TIMEOUT = (10, 30)`
  borne chaque connexion et chaque lecture de socket, jamais la lecture entière : six
  sauts de redirection valent six fois le budget, et une boucle de streaming n'est
  bornée par rien du tout. C'est ce qui produit les 57 s. Le remède est un **budget
  global** vérifié entre les sauts ET pendant la lecture, qui rabote au passage le
  timeout de socket sur ce qu'il reste — et dont le verdict **dit ce qu'il a tenté**
  (combien de sauts, où il en était), sans quoi l'agent ne peut pas décider s'il
  réessaie.

⚠️ **À vérifier sur tout `async def` de `tools/`** dont l'await est conditionnel ou en
fin de corps : c'est exactement la forme qui passe le garde-fou.

### Même porte dérobée, côté capacités cette fois (oto-backend#867, 04/09)

Le correctif du seam (mode n°4, plus bas) range les handlers **sync** hors boucle sur
`inspect.iscoroutinefunction(cap.handler)` — un handler **async** reste dans la boucle,
À DESSEIN (34 capacités le sont réellement). Mais `connectors.identities` (`_list`) est
`async def` et appelait NÛMENT `connector_identities.list_identities(...)`, qui pour
Unipile faisait 1 (liste) + N (statut live, un par compte hébergé) appels HTTP
`requests` — synchrones, timeout de lecture **120 s par appel** (`oto/tools/unipile/
const.py`, jamais exposé au-dessus). Résultat mesuré le 04/09 : **87,8 s de gel total
de la production** (MCP + REST + sondes de veille) pour l'ouverture d'une seule page du
dashboard, et un appel ≥ 20 s CHAQUE jour où la page est ouverte depuis fin août — le
seam ne pouvait rien y voir, la porte est dans le CORPS du handler async, pas dans sa
forme.

**Le remède, backend seul (le client oto-core n'expose pas de `timeout` par appel) :**
`_call_unipile` (`oto_mcp/connectors/identities.py`) enveloppe chaque appel Unipile
synchrone en `asyncio.to_thread` (hors boucle) **et** `asyncio.wait_for` borné à **25 s**
— mesuré défendable contre les deux populations observées (2-4 s en temps normal,
19,9-23,3 s les jours lents qui répondaient quand même, 46,2-87,9 s les jours qui ont
gelé) : large marge sur le nominal, coupe ferme les pathologiques. Le thread lancé
continue jusqu'à sa vraie fin (impossible d'interrompre un `requests` en cours), mais
l'APPELANT reçoit `TimeoutError` — converti par la capacité (`_list`/`_set_default`,
`oto_mcp/capabilities/connectors/identities.py`) en `AuthzDenied(502,
"unipile_list_failed", …)`, même vocabulaire que `unipile_seats.py`. **Un Unipile lent
rend désormais une erreur nommée, plus un gel.** `_unipile_select` (jumeau exact,
`oto_identity op=set`) reçoit le même traitement.

Effet de bord assumé : la liste BYO n'avale plus silencieusement une panne Unipile en
`accounts = []` (l'ancien `except Exception` portait, à tort, le commentaire d'une
sonde de statut annexe) — c'est ICI la liste elle-même, un `[]` muet valait le défaut
d'oto#42 (un agent concluait « aucun compte » au lieu de « Unipile n'a pas répondu »).
Le fail-soft de la sonde de statut live (`_unipile_live_status_map` → `{}`/« ok »),
LUI, reste inchangé — documenté, intentionnel, hors du périmètre de ce lot.

⚠️ **Reste ouvert (lot 2, non traité ici)** : les 34 sondes de connecteur (`verify.py`),
la ligne `unipile_connect.py:244` (jumelle non protégée de `hosted_auth_link` juste à
côté), le segment Browserbase (`start_session`/`release_session`), `web.py` cran ③, et
trois callbacks OAuth + deux DCR côté REST — même classe, chemins distincts. Lot 3 (le
garde-fou qui ferme la classe entière) non plus.

### Neuf routes REST FOD, même classe (oto-backend#867 lot 2, 04/09)

`api/sirene.py` (6 routes) et `api/accords.py` (3 routes) sont des routes Starlette
`async def` qui appelaient le client FOD (`fod/http.py`, `httpx.Client` **synchrone**,
read timeout **100s partagé par tous les clients FOD** — un scan SIRENE légitime peut
en avoir besoin, donc **non modifié**) nûment. Même porte dérobée que la liste
d'identités Unipile : `async def` visible, appel bloquant plus bas, invisible à un
`grep` du fichier (le client HTTP vit dans un module partagé, pas dans `api/sirene.py`).

**Le remède, local aux deux fichiers d'API (`_fod`, calqué sur `_call_unipile`
du lot 1)** : `run_in_threadpool` (même primitive qu'`api/zoho.py:85`, le contre-exemple
qui montrait déjà la discipline) + `asyncio.wait_for`, avec **deux paliers** plutôt
qu'un délai unique — les neuf routes n'ont pas le même profil :
- **`_FOD_TIMEOUT_FICHE_S = 20`** — fiche unique (siege/siret/etablissements/info côté
  SIRENE, themes/get_one côté accords) : ne scanne jamais plus d'un enregistrement,
  doit répondre en une fraction de seconde en fonctionnement normal.
- **`_FOD_TIMEOUT_SCAN_S = 60`** — recherche paginée et surtout `headquarters` (jusqu'à
  10 000 SIREN en UN scan) : un vrai lot volumineux peut légitimement approcher les
  dizaines de secondes, une borne à 20s l'aurait cassé pour de vrai.

Au-delà du délai, `asyncio.TimeoutError` devient un `504 fod_timeout` nommé (pas de
gel, pas de 500 générique). Banc `tests/test_fod_rest_routes_hors_boucle.py` : neuf
routes, deux crans chacune (thread observé + timeout → 504), plus le même contrôle
qui mord qu'au lot 1 (neutralisation vérifiée empiriquement).

### Le rafraîchissement du jeton Google, sous douze tools (oto-backend#867 lot 2)

`gmail.py`/`drive.py`/`sheets.py`/`tasks.py`/`calendar.py`/`chat.py` (deux tools
`async def` chacun) appellent tous `_client_for_user(account)` — qui résout les
credentials Google (`auth/google.py::credentials_for`) et, si l'access token stocké
est expiré, fait un `_refresh_access_token` **synchrone** (`requests.post`, 15s)
avant de construire le client. Les appels à l'API Google, EUX, sont déjà en
`asyncio.to_thread` dans chacun des 12 tools — c'est précisément ce qui a fait
écarter ces chemins d'un premier balayage naïf ; seule la construction du client
(donc le refresh, quand il a lieu) tournait encore nûment dans la boucle.

**Remède, dupliqué dans les six fichiers** (comme `_client_for_user` lui-même
l'est déjà — même granularité, pas de module partagé nouveau) : un
`_client_for_user_async` qui enveloppe l'appel sync en `asyncio.to_thread` +
`asyncio.wait_for(20s)`. 20s et non un nouveau timeout plus court : le socket
timeout existant (15s, `auth/google.py::_refresh_access_token`) est déjà court —
le remède est d'abord « sortir de la boucle », le délai REST ne fait que ne pas
préempter l'échec naturel de la requête. Chaque fichier garde son propre
vocabulaire d'erreur (`_bad(...)` où il existe, `McpError(ErrorData(...))` sinon,
cf. `calendar.py`/`sheets.py`) plutôt que d'en introduire un nouveau.

Banc `tests/test_google_token_refresh_hors_boucle.py` : six modules × deux crans,
neutralisation vérifiée empiriquement — même méthode que les deux lots précédents.

## Le mode n°2 a une SECONDE porte : les middlewares MCP (incident du 15/08)

Même mode de gel (DB sync dans la boucle), autre famille de call-sites — et celle-là
passait sous les deux garde-fous. Sous la charge d'une campagne (~8 clients lourds :
3 workers runner, 4 agents locaux, un appariement qui écrit), 502 en rafale sur
`mcp.oto.cx` + 807 « Unexpected ASGI message after response already completed » en
20 min, **CPU calme** (load 0,31) — signature du gel de LOOP, pas de la saturation.

> ⚠️ **Un des trois indices ci-dessus a été mal attribué, et il faut le savoir avant de
> le réutiliser.** Les 807 « after response already completed » ne viennent PAS du gel :
> c'est la signature de la race terminate-vs-POST documentée plus bas (#352), qui tournait
> en parallèle et dont on ignorait alors l'existence. Le gel, lui, est bien établi par le
> py-spy ci-dessous — ça, ça tient. Ne garder de cette ligne que les 502 et le CPU calme.

py-spy sur la box, MainThread, **3 relevés sur 6** (dont ≥4 s consécutives) :

```
psycopg execute ← has_credential (credentials_store.py) ← has_member_api_key
  ← walk_cascade (access/cascade.py) ← status_for ← _resolve_context (instructions.py)
  ← _c_layers ← session_layers ← compose_session
  ← on_initialize (middleware/…)          ← LE chemin async
```

`DynamicInstructionsMiddleware.on_initialize` composait l'artefact A/C **dans la
boucle**, à CHAQUE handshake. Or `compose_session` marche `access.status_for`, donc la
cascade de résolution de **tous** les connecteurs : plusieurs requêtes par connecteur,
sur une DB distante. Un `initialize` = un gel de tout le serveur pendant la composition.
Correctif minimal : la composition (et celle du projet publié) part en
`run_in_threadpool` — les ContextVars sont propagées (anyio `copy_context`), patron déjà
en place dans le même fichier pour `_reachable_suffix`.

**Pourquoi `test_no_blocking_async_handlers` ne pouvait PAS l'attraper** — deux raisons
indépendantes, à connaître avant de croire un chemin couvert :
1. il énumère `m.list_tools()` : un middleware n'est pas un tool, il n'est jamais regardé ;
2. son critère est « contient un `await` dans son propre scope ». Un hook de middleware
   **doit** `await call_next(context)` → il passerait le critère même énuméré. Le blocage
   arrive APRÈS cet await, dans le même scope.

D'où un garde-fou de nature différente, `tests/middleware/test_no_blocking_db_in_middleware.py` :
il n'analyse pas le source, il **observe le thread**. Le seam unique d'emprunt de
connexion (`db._conn._get_pool`) est remplacé par un mouchard qui note le thread
appelant puis refuse ; le chemin est vert ssi il a **réellement** tenté d'atteindre la
base (sinon la garde est inerte — le vert ne vaudrait rien) et qu'aucune tentative ne
vient du thread de la boucle. Profondeur quelconque, aucune whitelist. Un test de
contrôle vérifie que la garde MORD (le même travail appelé nûment dans la boucle est
bien attrapé).

⚠️ **Reste à traiter, MÊME classe, non couvert** : le combinateur d'autz `ORG_MEMBER` de
`capabilities/_authz.py` appelé depuis `_rest_adapter._handler` (`current_org` sync,
relevé py-spy) — UNE requête, là où la composition en faisait des dizaines, d'où l'ordre
de traitement qui l'a laissé pour la fin.

✅ **Traité le 2026-09-09** (`DynamicInstructionsMiddleware.on_list_tools`, org + index
guide + index guides, sync) et le sink du calllog `server._calllog_sink` →
`auth.hooks.current_user_sub_from_token` → `db.upsert_user` (écriture + commit dans la
boucle). ⚠️ Ce reste-à-traiter était **sous-estimé d'un facteur cinq** : le sink n'était
qu'UN des sept demandeurs de la même identité dans le même appel. Cf. le mode n°5.

✅ **Traité le 2026-09-14** : `UserDisabledToolsMiddleware.on_initialize` →
`session_visibility.compute_hidden_layers` (`async def` qui faisait, DE PART ET D'AUTRE
de son unique `await` réel — `ctx.fastmcp.list_tools`, laissé dans la boucle,
légitimement async —, une bonne dizaine de lectures/écritures PG synchrones : toggles
perso, rôle plateforme, denylists admin/équipe, activation connecteur, RBAC org et
équipe, sélection marketplace avec son seed en écriture, option bêta). Rejoué sur un
`initialize` complet plutôt qu'un appel isolé, py-spy aurait montré le même patron que
`compose_session` le 15/08 : chaque handshake gèle le serveur entier le temps du calcul.
Remède identique au reste du mode n°2 : le corps sync est coupé en deux fonctions pures
(`_resolve_toggle_context`, `_compute_couches`), appelées via `run_in_threadpool` de part
et d'autre du seul `await` — aucune des deux ne lit ni n'écrit de ContextVar (vérifié :
`access.current_org`/`current_group` ne font que LIRE celles de `session_org.py`,
`connector_selection.seed_active` ÉCRIT en base, jamais en ContextVar). Garde-fou :
`tests/middleware/test_no_blocking_db_in_middleware.py` (nouveau test,
`test_user_disabled_tools_on_initialize_ne_touche_pas_la_base_dans_la_boucle`) — 6 accès
DB depuis le thread de la boucle avant correctif, 0 après.

✅ **Traité le 2026-09-15** : `SentryToolErrorMiddleware.on_call_tool`
(`oto_mcp/sentry_setup.py`) — pas du DB cette fois, mais le même mode : `capture_exception`
tournait nûment dans la boucle sur le chemin d'erreur de **chaque** appel de tool.
Diagnostiqué sur les timeouts urllib3/`requests` de `linkedin_aiark_person` (2 tentatives
de 30 s, `_appel_avec_reprise`) : sur 30 échecs en production, les 30 portaient un
`sentry_event_id` (donc 30 captures dans la boucle), mais seulement 5 dans la même fenêtre
ont mesuré un gel ≥1 s — le coût de la capture VARIE, il fallait le chiffrer plutôt que le
supposer. Chiffré sur la VRAIE chaîne d'exception (un socket qui pend pour de vrai,
`requests.exceptions.ReadTimeout` dont `__context__` est un authentique
`urllib3.exceptions.ReadTimeoutError` — vérifié, pas fabriqué à la main), Sentry en DSN
vide (`transport=None`, donc `Client.capture_event` construit l'event ENTIER — pile,
filtrage in-app, `linecache` pour le contexte source de chaque frame — et ne court-circuite
que la dernière ligne, l'envoi) : médiane 6-9 ms, un appel « à froid » (1ᵉʳ après boot,
`linecache` lit chaque fichier une fois) jusqu'à ~50-70 ms sur
`tests/test_sentry_capture_hors_boucle.py`. **Ce chiffre, à lui seul, n'explique pas des
gels ≥1 s** — l'ordre de grandeur est trop petit ; les gels mesurés en prod tiennent
probablement à la coïncidence avec d'autre I/O sync sur la même boucle au même instant.
Mais c'est quand même de l'I/O disque synchrone posée nûment dans la boucle, sur le
chemin d'erreur de CHAQUE tool — la règle mono-loop ne se négocie pas au chiffre moyen.

Remède : seul `sentry_sdk.capture_exception(e)` part en `run_in_threadpool` — la création
du scope et la pose des tags/user (`mcp.tool`, `mcp.client`, `user.id`) restent dans le
contexte ASYNC, avant l'entrée dans le thread. Vérifié EMPIRIQUEMENT (pas supposé) que le
scope Sentry — porté par une ContextVar interne au SDK — survit à la copie de contexte que
fait `run_in_threadpool` (anyio) : un event réel, capturé DANS un thread du pool via un
transport collecteur (aucun réseau), porte bien les tags posés dans le contexte appelant
avant l'`await`
(`tests/test_sentry_capture_hors_boucle.py::test_le_tag_pose_avant_le_thread_est_sur_levent_capture_dans_le_thread`).
`_LAST_EVENT_ID.set(...)` (la ContextVar OTO, jamais celle du SDK) reste posée dans le
contexte ASYNC, après le retour du thread — même piège que documenté pour
`session_visibility.py` la veille : une écriture faite DANS le thread ne remonterait pas.
Garde-fou rouge→vert :
`tests/test_sentry_capture_hors_boucle.py::test_capture_exception_tourne_hors_du_thread_de_la_boucle`
(même mouchard que les autres — thread observé, pas le source), plus son contrôle qui mord.

## Mode n°3 — la requête est au BON endroit, mais elle est lente (incident du 27/08)

Les deux modes ci-dessus sont des erreurs de **placement** : du I/O sync là où il ne
devait pas être. Celui-ci n'en est pas une — le handler est `def` sync, donc routé en
threadpool comme la règle l'exige — et il gèle quand même, parce qu'**une requête assez
lente gèle depuis n'importe où**. Le threadpool borne la concurrence, pas la durée : sous
charge, les threads occupés par la même requête lente refluent sur la boucle (attente de
connexion au pool, sérialisation des callbacks), et `loop_watch` nomme la boucle tenue.

**Ce que ça donnait** (prod, 08:28→08:47 le 2026-08-27) : `mcp.oto.cx` et l'hôte d'un tenant tiers
injoignables, gels de **185 s toutes les ~3 min** — donc gelé en continu —, `PoolTimeout:
couldn't get a connection after 5.00 sec` en cascade, 29 connexions en attente d'accept
sur le socket, CPU calme. Les workers runner timeoutaient à 60 s et redémarraient, ce qui
**rejouait l'appel coûteux** : la panne s'auto-entretenait.

**Pourquoi la table de discrimination du §suivant n'aide pas ici** : ce mode a
exactement la signature du n°2 — `loop_watch` parle, py-spy montre MainThread dans
`psycopg execute`. Le seul moyen de les séparer est de **lire la stack jusqu'à la
requête** et d'aller l'`EXPLAIN` :

```
wait (psycopg/connection.py) ← execute ← project_run_stats (db/usage.py)
  ← audit_project ← _project (capabilities/projects.py)   ← handler SYNC, bien placé
```

**La cause** : `_runs_from_journal` retrouve la clôture d'un run par `args->>'run_id'` —
une **expression**, qu'aucun index ne portait. Chaque run reconstruit valait donc un
parcours complet de `tool_calls` : 639 ms et 911 882 lignes filtrées l'unité, × 9 350
runs. Remède = `idx_tool_calls_run_finish_ref` (index partiel d'expression, 624 kB) :
185 s → 268 ms sur le pire projet, 639 ms → 0,05 ms sur la sonde unitaire.

**Trois leçons, dans l'ordre où elles coûtent cher :**
- **Un JSONB interrogé dans un `WHERE` est un index d'expression qui manque.** La colonne
  homonyme (`tool_calls.run_id`) existait juste à côté et donnait l'illusion de couvrir le
  chemin — elle sert l'autre LATERAL, pas celui-là.
- **Sortir le I/O de la boucle ne corrige PAS ce mode**, il le déguise : le serveur
  répondrait, la lecture resterait à 185 s. Le réflexe des modes 1 et 2 est ici un
  contresens.
- **La lenteur suit le VOLUME, donc elle arrive sans qu'on ait rien changé.** Le coût
  était proportionnel au journal entier ; la campagne pilote a porté un projet à 96 % des
  runs de la plateforme, et le seuil a été franchi un matin, sans déploiement. Une lecture
  dont le coût ne se borne pas au scope demandé est une panne à retardement — d'où le
  second correctif du même lot : les deux lectures par projet poussent leur filtre DANS le
  CTE, au lieu de filtrer le résultat d'une reconstruction déjà faite pour tout le monde.

## Mode n°4 — le SEAM des capacités : un adaptateur async, 285 handlers sync dedans (incident du 2026-09-01)

**13 minutes de production coupée**, dont **12 min 48 s** sans une seule réponse : les
connexions étaient acceptées par le noyau et jamais traitées — **376 empilées** dans la
file d'attente. Pas un crash, pas de CPU, pas de 502 : le silence.

py-spy sur le processus vivant, sans le tuer :

```
wait (psycopg/connection.py:484)              ← bloqué sur la base
datastore_ensure_key_index (oto_mcp/db/datastore.py)
set_schema (oto_mcp/datastore/schema_ops.py)
patch_schema (oto_mcp/datastore/schema_ops.py)
_patch_schema (oto_mcp/capabilities/datastore/columns.py)   ← handler SYNC… dans la boucle
```

**Le piège, et il vaut d'être su hors de ce dépôt : une simple LECTURE peut geler la
production.** Une requête d'analyse lancée à la main tournait depuis 47 minutes. Elle ne
posait aucun verrou gênant — mais `CREATE INDEX CONCURRENTLY` attend, *par conception*,
la fin de toute transaction ouverte avant lui (`WaitForOlderSnapshots`) avant sa passe de
validation. La lecture retenait donc l'index, et l'index retenait la boucle. Reproduit
sur base jetable : lecture de 20 s ⟹ la pose rend la main à 19,6 s ; à 47 min, 47 min.

⚠️ Une transaction simplement `idle in transaction` en READ COMMITTED, elle, ne retient
PAS le CIC (elle ne tient plus de snapshot) — c'est la requête qui **tourne**, ou une
transaction REPEATABLE READ, qui le retient. Utile à savoir avant d'accuser la mauvaise
session dans `pg_stat_activity`.

### Pourquoi les trois garde-fous précédents étaient aveugles

Le tool MCP n'est pas écrit à la main : il est **fabriqué** par
`capabilities/_mcp_adapter._make_tool`, et son jumeau REST par
`_rest_adapter._make_handler`. Ces deux fabriques rendent un `async def` — il leur faut
la boucle pour les 34 capacités réellement asynchrones et pour le refresh de visibilité.
FastMCP et Starlette les laissent donc **dans la boucle**… et tout ce qu'elles appelaient
nûment avec : la règle d'autz (qui marche la cascade des rôles), le handler sync, et
l'écho d'org (deux lectures de plus sur chaque appel org-scopé).

- `test_no_blocking_async_handlers` : critère « ce `async def` contient-il un `await`
  dans son propre scope ? ». Le tool fabriqué en contient — c'est la porte dérobée déjà
  documentée plus haut pour `web_read`, sauf qu'ici elle ne concerne pas un handler mais
  **le moule de tous les handlers** ;
- `middleware/test_no_blocking_db_in_middleware` : n'observe que les middlewares ;
- la règle « un handler d'I/O est `def`, jamais `async def` » : **respectée**. Les 285
  handlers de capacité SONT sync. C'est justement pour ça qu'ils gelaient : la règle
  suppose que FastMCP route les sync en threadpool, ce qu'il fait pour un `@mcp.tool`
  qu'on lui donne — pas pour un sync appelé nûment depuis un `async def`.

Deux appels avaient déjà été rapiécés un par un (`node_view._node`, `node_edit.edit`
passent par `run_in_threadpool` depuis leur propre handler) : le symptôme était donc
connu, jamais son axe. Et `docs/` nommait depuis le 15/08 le combinateur d'autz
`ORG_MEMBER` « appelé depuis `_rest_adapter._handler` » comme reste-à-traiter — c'était
le même seam, vu par un seul de ses côtés.

### Le remède, en deux temps qui ne se remplacent pas

**① Sortir le travail de la boucle, AU SEAM.** Les deux adaptateurs rangent désormais
`autz + handler sync + écho d'org` dans `run_in_threadpool`. Un seul endroit, les deux
faces, les 285 capacités — plutôt que 285 rustines dont chaque nouvelle capacité
oublierait la sienne. Un handler `async def` reste dans la boucle (un thread n'a pas de
boucle où jouer une coroutine) ; le tri se fait au **montage**
(`inspect.iscoroutinefunction`). Les ContextVars traversent : `run_in_threadpool` (anyio)
exécute sur une **copie** du contexte, donc l'axe `_org`, le `view_as` et l'empreinte
client sont lus normalement depuis le thread — vérifié. Ce qu'une copie ne rend pas,
c'est une **écriture** de ContextVar faite dans le thread ; aucune capacité n'en fait
aujourd'hui.

**② Borner le DDL à chaud.** Sortir de la boucle ne suffit pas, et c'est le contresens à
éviter : hors boucle, le même `CREATE INDEX CONCURRENTLY` aurait tenu **un thread du
threadpool pendant 47 minutes** et laissé son appelant pendu. `_conn._connect_autocommit`
pose donc `lock_timeout` (5 s) et `statement_timeout` (60 s) — l'attente derrière une
transaction plus ancienne est une attente de VERROU (`ShareLock` sur le VXID), donc
`lock_timeout` la coupe ; mesuré 5,3 s au lieu de 19,6 s, et 0 s quand rien n'est devant.

Ce qui suit une borne, c'est **ce qu'on fait du travail resté à faire**, sans quoi on a
juste déplacé le problème :

- `datastore_ensure_key_index` lève `KeyIndexUnavailable` (typée) au lieu d'un 500
  opaque, et **nettoie l'index temporaire** que le CIC coupé laisse derrière lui — un
  unique INVALIDE continue d'imposer sa contrainte aux écritures suivantes ;
- `set_schema` / le provisionnement de slot **écrivent quand même le schéma** et le
  DISENT en avertissement : le chemin d'écriture applicatif continue de rapprocher les
  lignes sur la clé, seule la garantie anti-course manque ;
- `oto-mcp maintenance key-indexes` (timer, déjà en place) la repose au tir suivant —
  il balaie précisément les namespaces à clé déclarée dont l'index manque. Il appelle
  donc `bornee=False` : un travail de FOND a le droit d'attendre son tour, et le borner
  garantirait qu'un index sur une table très occupée ne se pose **jamais**.

### Le garde-fou

`tests/test_capacites_hors_boucle.py` — il n'analyse pas le source, il **observe le
thread** (même parti que celui des middlewares). Deux crans : le **seam** (ce que les
fabriques font d'un handler sync, énoncé général valable pour les 285 sans liste à
tenir) et un **cas réel sur une vraie base** — `data_patch_schema` du registre, joué en
entier jusqu'au `CREATE INDEX CONCURRENTLY`, avec l'assertion d'inertie (« le DDL a-t-il
seulement été atteint ? ») sans laquelle le vert ne vaudrait rien. Plus un contrôle qui
mord : le même travail appelé nûment dans la boucle EST attrapé. Rejoué contre le commit
d'avant le correctif : **5 rouges sur 8**, dont le DDL montré sur `MainThread`.

⚠️ **Ce que ce lot ne couvre pas** : un handler `async def` qui ferait de l'I/O sync
avant son premier `await` (la porte dérobée du mode n°1) reste possible sur les 34
capacités asynchrones — le seam les laisse dans la boucle, à dessein.

## Mode n°5 — la même valeur redemandée dix fois par appel (mesuré le 2026-09-09)

Les quatre premiers modes disent **où** l'I/O est posée. Celui-ci ne conteste le
placement de personne : il compte **combien de fois** la même valeur est reconstruite
dans un seul appel. Une requête au bon endroit qui part dix fois pour un résultat
identique coûte autant que dix mauvais placements.

`auth.hooks.current_user_sub_from_token()` canonicalise le `sub` du jeton dès que le
drain d'alias est armé (il l'est, et il le reste — `tenant_migration.py`) : un `SELECT`
sur `sub_aliases`, puis un `INSERT … ON CONFLICT` sur `users`, donc un **COMMIT**. Deux
requêtes synchrones, dans la boucle, **sans aucune mémoire** : chaque appelant les
repaie. Or sept intermédiaires redemandent la MÊME identité dans le MÊME appel — le nom
d'outil du tenant (`middleware/alias`), le refus de compte en pause
(`middleware/account_suspended`), le contexte d'appel (`middleware/call_context`), le
filtrage per-user (`middleware/disabled_tools`), les instructions d'org
(`middleware/dynamic_instructions`), et deux fois le journal (`server._calllog_identity`
et `_calllog_sink`).

**Mesuré sur la chaîne réellement servie**, faux pool comptant chaque requête et le
thread qui la lance, un `tools/call` en régime permanent : **10 allers-retours PG
d'identité, dont 5 COMMIT, tous depuis le thread de la boucle** — sur 14 allers-retours
au total pour l'appel. Soit **71 % du SQL d'un appel d'outil pour une seule valeur**.
`py-spy` sur la production le même jour : 38 % du temps de boucle occupé, dont les deux
tiers en SQL synchrone. ⚠️ Le journal, lui, n'en déclarait que 4 à 7 % — son témoin ne
compte rien sous une seconde (`loop_watch`, seuil 1 s) : **ne jamais conclure sur
l'ampleur d'un gel à partir des warnings**.

### Le remède, et sa borne

`middleware/identity_scope.py`, enregistré **le plus externe des nôtres** (au-dessus même
de `ToolAlias`, qui est le premier à demander l'identité ; `fastmcp` pose le sien avant
tout enregistrement, et il ne lit aucune identité) : il ouvre une portée par
message et la garnit **hors boucle** (`asyncio.to_thread`) — exactement le geste que
`_calllog_sink` faisait déjà pour son insertion, deux lignes plus bas, et qui avait
manqué l'identité juste au-dessus. **Après : 2 allers-retours par `tools/call`, 0 dans
la boucle.**

⚠️ **La borne est une frontière d'identité, et c'est elle qui compte.** Un cache
d'identité mal borné servirait le compte d'un utilisateur à un autre — infiniment pire
que le gel qu'on corrige. Trois barrières indépendantes : la **clé EST l'identité** (le
`sub` brut du jeton ; demander A ne peut pas rendre B), la portée pose un dictionnaire
**neuf** par message (jamais un défaut de module, et un `set` de ContextVar n'est pas vu
d'une tâche sœur), et **hors portée il n'existe pas** (défaut `None` ⟹ le chemin d'avant
à l'octet près). Un refus (`AliasNonResolvable`, `CompteEnPause`) n'est jamais mémorisé :
il se lève pour chaque demandeur, comme avant.

⚠️ **La pré-résolution ne lève rien.** Elle est plus externe qu'`ErrorEnvelopeMiddleware` :
son exception partirait sans l'enveloppe contractuelle. Un échec la laisse donc passer,
et le chemin paresseux d'avant se rejoue là où il se rejouait.

### Le garde-fou

`tests/middleware/test_identite_une_fois_par_message.py` — même parti que les deux
autres : on **observe**, on ne lit pas le source. Deux crans (la chaîne servie, puis la
MÊME chaîne privée de sa portée, qui doit repayer les dix) et la garde d'identité à
part. Épreuve de chute jouée le 2026-09-09 : **8 mutations, 8 rouges**, dont la clé de
cache rendue constante — la forme exacte de la fuite d'identité.

### La face REST n'était pas concernée

`api/base._authenticate` fait déjà les deux mêmes requêtes **par `run_in_threadpool`**,
et un handler REST qui invoque un tool pose un `sub_override` que
`current_user_sub_from_token` rend avant de regarder quoi que ce soit. Le défaut était
MCP-seul ; le correctif aussi.

## Mode n°6 — le travail est bien placé, bien borné… et refait pour rien (oto#82, 11/09)

Les modes précédents disent **où** l'I/O est posée et **combien de fois**. Celui-ci ne
conteste ni l'un ni l'autre : le geste est au bon endroit, dans sa borne, et il ne
devrait **pas avoir lieu du tout**. Le coût n'est pas sa durée, c'est le **verrou qu'il
demande sur une table partagée**.

`set_schema` décidait de reposer l'index d'unicité de clé métier sur `if new_key:` — donc
sur **ce qui existe**, jamais sur **ce qui a changé**. L'ancien schéma était bien relu
juste au-dessus, mais il ne servait qu'au relevé d'effacement : **jamais comparé à la
clé**. Conséquence, mesurée en comparant l'identifiant de relation de `ds_bkey_<ns>`
avant et après : changer un **libellé** reconstruisait l'index (DROP + CREATE
CONCURRENTLY + RENAME), précédé d'un `GROUP BY … HAVING COUNT(*) > 1` sur **toutes** les
lignes du tableau. Coût de la seule pose relevé dans l'issue : ≈ 0,55 s pour 400 000
lignes. Et `patch_schema` recopie la clé telle quelle dans le schéma résultant : **tout**
patch passait par là.

**Ce qui rend ce gaspillage dangereux, et pas seulement cher :** les index de clé sont
partiels par `ns_id`, mais la **table ne l'est pas** — tous les tableaux vivent dans
`datastore_rows`. Un `DROP INDEX` y prend un `AccessExclusiveLock`, et une demande de
verrou exclusif **se met en file DEVANT** les lecteurs et écrivains suivants. Modifier un
libellé sur un tableau arrêtait donc le voisinage, qui n'a aucun rapport avec le geste.
Un interblocage réel en est sorti le 2026-09-05 à 23:46 UTC, sur un appel qui ne portait
qu'un booléen de tête.

**Le remède tient en une comparaison** — la clé d'hier contre celle d'aujourd'hui — avec
deux moitiés qui ne se déduisent pas l'une de l'autre :

- « inchangée » ne veut pas dire « présente » : si l'index **manque** (borne coupée au
  tir précédent), il se repose quand même ;
- le **retrait** se décide sur l'**existant**, pas sur la déclaration d'hier : un index
  qu'aucun schéma ne déclare plus imposerait son unicité à une colonne que
  `data_get_schema` ne montre pas — un refus d'écriture que personne ne peut relier à sa
  cause.

**Et le retrait n'était pas borné.** La pose l'a été après l'incident du 2026-09-01 ; le
retrait est resté sur le pool de requête, où `lock_timeout` n'existe pas et
`statement_timeout` vaut 0. Mesuré : **5,72 s d'attente derrière une simple lecture de
6 s**, borne réglée à 300 ms et ignorée. Il passe donc sur la connexion bornée, comme la
pose, et lève `KeyIndexStillEnforced` quand il renonce — sœur de `KeyIndexUnavailable` et
son contraire : là une contrainte manquait, ici une contrainte **survit** à la clé qui la
déclarait. Le schéma est déjà écrit dans les deux cas, donc les deux se **disent** en
avertissement plutôt qu'en 500, et celui du retrait **nomme la clé** qui reste imposée.

### Le garde-fou

`tests/test_pose_index_bornee.py` — bancs à vraie base qui mesurent l'**identifiant de
relation** de l'index avant et après, seule mesure qui distingue « reposé » de « laissé
en place ». Rejoué contre le corps d'avant : **un rouge**, l'index reconstruit sur une
pose de libellé. Et chaque moitié de la condition a sa propre chute, simulée en mémoire :
neutraliser « et si l'index manque » rougit le banc de l'index absent, réduire le retrait
au souvenir de la déclaration rougit celui de l'index orphelin.

### Ce que ce lot ne couvre pas

- `oto-mcp maintenance key-indexes` ne balaie que les index **MANQUANTS** des tableaux à
  clé déclarée. Un index **orphelin** — clé retirée, retrait coupé par sa borne — n'est
  repris par personne ; seule une nouvelle pose du même schéma le retire. C'est pourquoi
  l'avertissement servi dit « repose le même schéma », et ne promet pas la maintenance ;
- le nom de l'index temporaire reste **déterministe et partagé par tableau**
  (`ds_bkey_<ns>_v2`) : deux poses concurrentes qui changent **toutes les deux** la clé
  visent encore le même objet. La fenêtre est désormais étroite (il faut deux
  changements de clé simultanés sur le même tableau) mais elle existe, et rien ne
  sérialise ce chemin ;
- `DROP INDEX CONCURRENTLY` (`ShareUpdateExclusiveLock` au lieu d'`AccessExclusiveLock`)
  retirerait la cause du verrou exclusif plutôt que sa fréquence. Non pris dans ce lot :
  il laisse un index INVALIDE quand il est interrompu, donc il demande son propre
  nettoyage et sa propre preuve.

## Un 502 en rafale n'est pas forcément un gel — la 2ᵉ cause (#352, nuit du 15-16/08)

⚠️ **À lire avant de conclure « c'est encore le gel ».** Ce document a servi, du 15/08 au
16/08, à attribuer au gel mono-loop des 502 qui n'en venaient pas. Les deux causes
partagent la moitié de leur signature — 502 en rafale, CPU calme, « after response
already completed » au journal — et se distinguent nettement sur un point : **la durée
des 502**.

| | gel mono-loop (ci-dessus) | race terminate-vs-POST (#352) |
|---|---|---|
| durée des 502 | **longue** (la requête attend la boucle) | **~0,2 s** — la connexion meurt aussitôt |
| loop_watch | callbacks ≥1 s nommés au journal | **muet** (la boucle tourne) |
| py-spy pendant | MainThread dans `psycopg execute` | MainThread **idle** |
| remède | sortir le I/O de la boucle | compléter la réponse ASGI |

**La mécanique, en DEUX étages** — et le premier n'est pas celui qui casse. Un POST
`/mcp` est en vol quand la session streamable-http se termine (le DELETE du client ferme
le stream mémoire du transport) :

1. `writer.send()` lève `anyio.BrokenResourceError`. **Le SDK l'attrape et la logue**
   (`ERROR mcp.server.streamable_http: Error handling POST request`) — elle ne s'échappe
   jamais. C'est du bruit, pas la panne ;
2. son `except Exception` tente alors d'écrire un 500 **par-dessus le 202 déjà envoyé**
   (`streamable_http.py:654`). h11 a clos la réponse → `RuntimeError: Unexpected ASGI
   message 'http.response.start' sent, after response already completed`. **C'est CELLE-LÀ
   qui remonte jusqu'à uvicorn**, et rien d'autre.

Mesuré sur 8,4 h de journal (15/08) : 2744 `BrokenResourceError` loguées, 1433
« Exception in ASGI application » — **toutes** le `RuntimeError` ci-dessus. Zéro
`ExceptionGroup`, et zéro « ASGI callable returned without completing response ». Le
piège de diagnostic est là : on cherche `BrokenResourceError` en haut de pile, elle n'y
est jamais — elle n'apparaît que dans le message logué par le SDK.

uvicorn **ferme alors le transport**. Et c'est là qu'est le vrai dégât : Caddy
tenait cette connexion pour réutilisable dans son **pool keep-alive**. Elle meurt sous
lui → 502 sur elle, **et sur les requêtes voisines qui en héritent** — d'où des 502 sur
des `claim`/`extend`/`thread_append` de workers qui n'ont jamais parlé à `/mcp` (4-5 runs
tués vers 00:05 UTC le 16/08). Le 502 ne frappe pas que le client parti.

**Le remède, dans NOTRE couche** : `oto_mcp/client_disconnect_guard.py`, posé par
`server.build_root_app` en couche la plus externe (entre uvicorn et le dispatch par
Host). Il complète la réponse à la place du client parti — 202 vide si les en-têtes ne
sont pas partis, fin de corps sinon — et **n'attrape que la classe « déconnexion client »**
(`error_taxonomy._is_client_disconnect`, le même prédicat que le drop Sentry) : toute
autre exception traverse intacte, ce que `tests/test_client_disconnect_guard.py` fige.
C'est la condition pour qu'une garde qui avale une exception soit acceptable.

**Pas de fix upstream à attendre** (vérifié le 16/08) : le site fautif de
`mcp/server/streamable_http.py` est identique de 1.27.2 à 1.29.0, à `v1.x` HEAD et à
`main`/2.0.0 — aucune version publiée ne garde ce `writer.send`. La PR upstream qui le
ferait (#2983) est ouverte depuis juin, jamais mergée ; le backport du fix voisin sur la
ligne 1.x a été refusé (`not_planned`, #3142), et `v1.x` est en « security fixes only ».
**Bumper `mcp` ne rapporte pas ce fix** — et 2.0.0 est hors d'atteinte tant que
`fastmcp-slim` pin `mcp<2.0`.

### La source de la churn : c'est NOTRE workload, pas un tiers

Les 3 IP Azure de l'incident portent `User-Agent: MistralAI-MCPClient/1.0` (+ un en-tête
`X-Internal-Service` qui nomme un service interne de l'appelant) et sont
**pleinement authentifiées** — zéro 401, zéro 403 sur 16 122 requêtes en 8,4 h. C'est le
client MCP hébergé de Mistral, exécutant notre propre charge d'enrichissement (38 782
appels sur 48 h : `data_write`, `data_rows`, `data_claim_next`, `serper_*`, `fr_*`).

**⇒ Ni intrusion, ni abus : aucun rate-limit à poser.** En throttler serait throttler la
campagne. Ce qui coûte, c'est le patron du client : il ouvre une session neuve **par
tour** puis la DELETE aussitôt — **16 213 `initialize` en 48 h**, durée de vie médiane
**339 ms** (p10 237 ms, min 211 ms), ~4,3 requêtes HTTP par session. Chaque `initialize`
paie tout le handshake. Le levier est la réutilisation de session côté client, pas le
reverse proxy.

⚠️ **Angle mort d'observabilité relevé au passage** : `/etc/caddy/Caddyfile` n'a **aucune
directive `log`** → pas d'access log Caddy. Les statuts ne se lisent que dans l'access log
uvicorn (`journalctl -u oto-mcp`), qui ne remonte qu'à ~8 h (cap 183 Mo, cron d'hygiène
disque) : **la rafale de nuit n'était déjà plus lisible au matin**, seule la base l'a
gardée. Compter des 502 côté edge est aujourd'hui impossible.

Deux corrélations qui NE marchent pas, à ne pas retenter : `Mcp-Session-Id` (32 hex, id
de transport MCP) ≠ `tool_calls.session_id` (UUID 36, session applicative) — espaces
disjoints, intersection nulle ; et le `X-Request-Id` de l'Envoy client ≠
`tool_calls.request_id`. Le pont qui marche est le **profil horaire** (ratio ~4,3:1 entre
requêtes HTTP et `initialize`) plus le `clientInfo` du handshake, stocké dans
`tool_calls.args` — noter que le connecteur Mistral ne surcharge pas le clientInfo du SDK
Python, donc la base ne montre qu'un générique `client_name='mcp'` : **le nom du produit
ne se lit que dans le User-Agent HTTP**.

## Corollaire de méthode (27/08)

Le 3ᵉ mode n'est pas un I/O mal placé mais une **requête lente** (JSONB sans index
d'expression : 185 s de boucle tenue, prod + tenant tiers à terre) — même signature py-spy
que le 2ᵉ, remède opposé : indexer, pas déplacer. Corollaire de méthode : **une lecture dont
le coût suit le VOLUME TOTAL et non le scope demandé est une panne à retardement, qui se
déclenche sans déploiement.**

**Ajout du 2026-09-01 (mode n°4).** Deux corollaires de plus, et le second est celui qui
a coûté treize minutes :

- **Un garde-fou qui énumère des objets écrits à la main ne voit pas ce qui est
  fabriqué.** Les trois gardes existants regardaient des handlers, des middlewares, des
  tools — jamais le MOULE qui en produit 285. Quand un lot introduit une fabrique,
  c'est la fabrique qu'il faut garder, pas ses produits.
- **Chercher l'axe, pas le call-site.** Le correctif « rapiéce ce handler-là » avait
  déjà été appliqué deux fois (`node_view`, `node_edit`) sans que personne ne remonte
  d'un cran. Deux rustines au même endroit sont le signal qu'on répare un cas là où vit
  une classe.

## Procédures : dispatch mixte sync/async

Une console `async` peut choisir une branche synchrone : les lectures, listes et
écritures de procédures faisaient encore du SQL dans la boucle, même après le
correctif des adaptateurs. `capabilities/_execution.execute` exécute désormais la
préparation et le handler synchrone dans le même thread ; un résultat awaitable
est attendu dans la boucle de l'appel. Les deux adaptateurs et les consoles de
procédures empruntent ce seuil, sans répéter l'autorisation.

Les handlers de lecture/écriture séparent leur I/O synchrone de l'enrichissement
asynchrone du manifeste d'outils. Les ContextVars déjà posées par l'appel sont
copiées vers le thread ; les faits produits par l'autorisation reviennent dans
`ResolvedCtx`, pas par une mutation de ContextVar dans le thread.
`tests/orgs/test_instruction_execution.py` exerce les quatre opérations admin
sur les deux faces, compte une autorisation, observe le thread des stores et
celui du manifeste, et vérifie le contexte et les refus. Il ne mesure aucune
latence de production.

## Observabilité — capturer la VRAIE pile d'un blocage, pendant qu'il a lieu (`hang_watch.py`, 15/09)

`loop_watch.py` (aiodebug `log_slow_callbacks`) nomme un callback bloquant et sa durée,
mais jamais QUELLE LIGNE de code tenait la boucle — le nom rendu est générique
(`TaskHandle._run_coro()`), inutilisable pour diagnostiquer. La raison tient au patron
lui-même : `on_slow_callback` est appelé **après** que le callback a fini — la pile
intéressante s'est déjà déroulée, `sys._current_frames()` à ce moment-là ne montrerait
que la sonde elle-même.

`aiodebug` porte un second mécanisme, `hang_inspection`, jamais branché ici. Il résout
le problème par un **thread séparé** qui surveille un timestamp partagé, mis à jour par
une tâche minuscule de la boucle : si le délai dépasse le seuil, la boucle est bloquée
**en ce moment**, et c'est ce thread — qui tourne PENDANT le blocage, pas après — qui
dump la vraie pile. Adapté dans `oto_mcp/hang_watch.py`, sur le même patron mais pas la
même sortie ni le même filtre :

- **journalise** (logger `oto_mcp.loop`, comme `loop_watch.py`) plutôt qu'écrire des
  fichiers `stacktrace-*.txt` sur la box (le patron d'origine) ;
- filtre au **seul thread principal** (celui qui fait tourner la boucle asyncio) — les
  autres threads du process sont le threadpool, jamais la boucle ;
- **aucune variable locale** (`traceback.extract_stack`, jamais `format_exc`) — même
  famille de risque que le fix Sentry du 2026-09-15 (#564, `include_local_variables=
  False`) : un secret déchiffré qui traînerait dans une frame ne doit jamais atteindre
  un journal ;
- **débit limité** : un état d'ÉPISODE (armé tant que le délai reste dépassé, réarmé à
  la résorption) garantit UN dump par blocage continu, et un plafond de **3 dumps par
  minute toutes causes confondues** évite qu'une salve de blocages courts et rapprochés
  (nuit du 14-15/09 : ~6 en une minute, suite à une bascule) ne remplisse le journal de
  piles redondantes du même incident — au-delà du plafond, le refus se DIT
  (« dump ignoré (plafond de N/min atteint) »), jamais en silence ;
- même seuil que l'existant : `OTO_SLOW_CALLBACK_WARN` (déf. 1.0s), pas un second réglage
  à tenir à jour séparément ;
- **interrupteur dédié `OTO_HANG_WATCH_ENABLED`** (déf. **actif**) : coupe le mécanisme
  par un redémarrage du process, sans redéployer. Actif par défaut parce que le
  mécanisme EST la réponse aux gels non identifiés de ce document — le désactiver par
  défaut reviendrait à se priver de la preuve le jour où elle sert ; défendable
  seulement parce que le coût est mesuré, pas supposé (ci-dessous). Nom aligné sur le
  patron des boucles de fond (`OTO_SCHEDULER_ENABLED`…), pas sur celui des seuils.

**Câblage** : `hang_watch.run_heartbeat_loop` est déclarée comme une `Boucle` de
`boucles_de_fond.py` (`tiers=False`, armée par `hang_watch.enabled()`) plutôt que
composée à la main dans `server.main` — pas parce qu'elle drainerait du travail en
base (elle n'en draine aucun), mais parce que `server.main` ne démarre **rien** qui ne
passe par ce registre (`tests/test_boucles_de_fond.py::test_server_main_ne_compose_
rien_hors_du_module`) : ajouter une tâche de fond, c'est l'ajouter là, un seul endroit
pour lire le roster complet de ce qui tourne. `tiers=False` sans nuance : elle ne
touche jamais un tiers, donc elle tourne dans TOUS les environnements — préprod
comprise, exactement là où le prochain gel non identifié peut survenir.

**Coûts, mesurés** (`tests/test_hang_watch.py`, doctrine du projet : chiffrer, pas
supposer) :
- le geste côté boucle (`beat()`, une écriture de flottant) : **~0,1 µs/appel** —
  sans commune mesure avec un tour de boucle asyncio ;
- le réveil périodique du thread watchdog lui-même, sous une charge SYNTHÉTIQUE qui
  sature la boucle (pas un repos total) : le coût SUIT LA FRÉQUENCE DE RÉVEIL — pas
  du bruit de machine, un coût réel et attendu du mécanisme à haute fréquence.
  `tests/test_hang_watch.py::test_cout_du_thread_watchdog_sous_charge_simulee` pose
  volontairement un intervalle resserré (10 ms, donc un réveil toutes les 5 ms —
  `interval/2`) pour exagérer la fréquence et rendre le coût mesurable, très loin du
  seuil de prod. Mesuré isolément à ce réveil de 10 ms : **12,7 % de CPU pour le
  thread sur 10s, et le débit de la boucle perd 9 %** ;
- **le même coût, au seuil réel de prod** (`/proc/self/task/<tid>/stat`, demande de
  la session de déploiement le 15/09/2026, avant d'activer par défaut en prod) :
  `OTO_SLOW_CALLBACK_WARN=1.0s` (ni surchargé ni absent des `.env` vérifiés), soit un
  réveil toutes les 500 ms — 100× moins fréquent qu'au banc ci-dessus —, fenêtre de
  180s, 360 réveils du watchdog — **0,020s de CPU (utime+stime) sur 180s, soit
  0,011 % d'un cœur**. Le coût suit la fréquence de réveil : moins de réveils par
  seconde, moins de travail pour le thread, rien de mystérieux — et c'est CE
  chiffre-là, au seuil qui tourne réellement en prod, qui est sans commune mesure
  avec le coût d'un aller-retour réseau ou DB qu'un vrai handler paierait de toute
  façon (cf. le chiffrage `capture_exception` du même jour, 6-9 ms).

**Limite connue : le watchdog détecte un délai, pas une cause — la pile capturée
peut être un témoin innocent.** Une boucle SATURÉE par de nombreux petits callbacks
qui s'enchaînent sans qu'aucun ne dépasse seul le seuil retarde le battement du
timestamp exactement comme le ferait un vrai blocage long : le délai cumulé finit
par dépasser `OTO_SLOW_CALLBACK_WARN`, un dump part — mais la pile qu'il montre est
celle du callback qui tourne AU MOMENT du dump, pas la somme des callbacks qui ont
saturé la boucle avant lui. Ce callback-là n'est coupable de rien ; il était juste
de passage. Ce n'est pas hypothétique : c'est le régime probable au palier de
charge actuel de la prod (0,8-0,9 cœur), où plusieurs handlers courts peuvent
s'enchaîner sans qu'aucun ne soit individuellement lent. Le lecteur d'un prochain
dump doit se poser la question avant de désigner un coupable : la pile capturée
EST-elle la cause d'un seul long blocage, ou seulement le témoin pris dans un
embouteillage de callbacks courts qui, ensemble, ont fait dériver le battement
au-delà du seuil ?

**Preuve empirique du garde-fou** (pas un banc vert de circonstance) :
`tests/test_hang_watch.py` provoque un VRAI blocage (`time.sleep` synchrone dans la
boucle, pas une exception fabriquée) et vérifie que le dump journalisé montre la
ligne exacte du `time.sleep`, sans aucune variable locale ; qu'un fonctionnement
normal (boucle occupée mais jamais bloquée au-delà du seuil) ne déclenche jamais de
dump ; qu'un blocage continu observé à plusieurs cycles de vérification ne produit
qu'UN dump ; que deux blocages séparés par une vraie résorption en produisent bien
DEUX (le réarmement) ; et qu'une salve au-delà du plafond par minute est refusée en
le disant.

## Le convoi du GIL — quand le travail est déjà bien threadpoolé (oto-backend#980, 16/09)

Différent des cinq modes ci-dessus : ici le travail est déjà correctement sorti de la
boucle (`run_in_threadpool`), et pourtant deux requêtes concurrentes sur le même
tableau se dégradent bien au-delà du linéaire. Remonté par scout : une lecture
paginée complète du datastore (8 910 lignes, ~90 colonnes) prend **2-3 s en solo**,
mais **54 s** à deux requêtes concurrentes sur le même token — pas un simple partage
de bande passante.

Reproduit sur la box (script isolé, contre la vraie base) : la même lecture prend
3,3 s seule, et **15 à 24 s** dès qu'UN SEUL thread de calcul tourne à côté, à
l'intervalle de bascule par défaut de CPython (5 ms — `sys.getswitchinterval()`).
C'est le **convoi du GIL** : à un intervalle de 5 ms, un thread qui vient de le
céder attend en moyenne bien plus longtemps pour le reprendre que le travail réel
ne le justifierait, sous contention. Resserré à **1 ms**
(`sys.setswitchinterval(0.001)`, posé dans `server.main()`, au tout début du
démarrage — jamais au niveau module), la même lecture retombe à **8,1-8,4 s**. En
dessous de 1 ms (testé à 0,5 ms), plus aucun gain : le plancher ×2 est déjà atteint.

Lot minimal (Alexis, 16/09) : ce seul réglage. Les pistes structurelles qui
resteraient à instruire si la contention redevenait un problème au palier de charge
suivant — lecture des grosses colonnes en `data::text` plutôt qu'en objets Python
détaillés, ou un modèle multi-processus — restent sur `oto-backend#980`, pas
attaquées ici. Preuve : `tests/test_switch_interval_980.py`, qui vérifie que `main()`
pose la valeur avant tout autre sous-système (capturée à l'appel de
`logging.basicConfig`, le tout premier après le réglage) — pas une mesure de perf
rejouée à chaque run (coûteuse, contre une vraie base), juste le fait qui compte :
la valeur est posée, et posée tôt.

## Mode n°1, encore : le handler `async` qui lit la base « juste avant » (`me.agent_context`, 21/09)

Coupure de prod d'environ **140 s** (relayée par oto cd). Cause trouvée dans
`capabilities/agent_context.py` : `_agent_context` est un `async def` et appelait
`_instructions.session_layers(...)` **nûment** — `_resolve_context` y fait du SQL
psycopg synchrone. `_execution.execute` ne met en thread que le handler **sync** ; pour un
`async def`, appeler le handler dans le thread ne fait que *construire la coroutine*, dont
le corps tourne ensuite sur la boucle. Le SQL tenait donc tout le process, MCP et REST.

**Correctif** : `await run_in_threadpool(_instructions.session_layers, sub, org_id)` — la
même discipline que `compute_hidden_layers` et `_get_guide`, qu'`_agent_context` appelle
juste à côté et qui, eux, étaient déjà protégés (d'où le trou : deux appels protégés
et un troisième oublié dans la même fonction).

**Preuve** : `tests/test_agent_context_hors_boucle.py` observe la boucle plutôt que le
source — pendant une lecture qui dort 0,5 s, une tâche incrémente un compteur toutes les
10 ms. Avant le correctif : **0 battement** ; après : ≥ 20. (Rejoué sur le clone avec
`PYTHONPATH=<clone>` : sans lui, l'*editable install* sert l'arbre partagé et le test
« passe » ou « échoue » sur le mauvais code — cf. `docs/commands.md`.)

**Balayage** (script AST jetable, 21/09) : les `async def` de `capabilities/`, `api/`,
`tools/` qui appellent, hors `run_in_threadpool`/`to_thread`, un module SQL (`db`,
`credentials_store`, `org_store`, `group_store`, `access`…). **71 appels directs dans 30
handlers.** Écartés à la main : 4 faux positifs (`access.current_user_sub_or_raise`, pure)
et les appels `access.current_project` / `account_noun` (purs eux aussi) ; `access.current_org`
et `current_group`, eux, descendent à la base (repli `get_active_org`, `is_member`). Corrigés
ici (une instruction chacun, patron identique) : `tools_me` `_list`/`_detail`,
`unipile_seats._list_seats`, `connectors/connect._connect`. Le reste est listé dans la PR
et **reste ouvert** — c'est le trou que ce document annonçait pour le mode n°1.

⚠️ **Ce que le balayage dit de la garde manquante** : un test qui rougit dès qu'un `async
def` appelle `db.*`/`credentials_store.*`/`org_store.*`/`group_store.*` hors threadpool est
faisable avec très peu de faux positifs **si `access.*` en est exclu** (mélange de pur et de
SQL — c'est là que naissent les faux positifs). Mais le stock existant est de ~26 handlers :
il faudrait un cliquet (liste de dettes qui ne peut que se réduire), pas un rouge sec.
La transitivité (`session_layers` → `_resolve_context` → SQL, exactement notre cas) n'est
pas décidable par nom sans graphe d'appels : un garde purement direct **n'aurait pas
attrapé ce gel-ci**.

## La garde d'exécution : `_connect()` sait s'il est appelé depuis la boucle (21/09)

Le gel du 21/09 (`me.agent_context`) est passé sous quatre garde-fous parce qu'aucun ne
regardait **où** s'exécute un accès base : l'AST « async sans await » ne voit pas un handler
qui `await` ailleurs, le seam `execute()` ne juge que le handler, `loop_watch` nomme après
coup. Le point de passage obligé de toute requête est `db._conn._connect()` (le pool n'est
ouvert nulle part ailleurs) : `oto_mcp/db/_hors_boucle.py` y pose la seule question qui
compte — **y a-t-il une boucle asyncio qui tourne dans CE thread ?** Un thread du
threadpool n'en a pas (`get_running_loop` lève), un `async def` en a une : le chemin
indirect (assistant sync appelé par un handler async, comme `session_layers`) est attrapé
sans rien savoir du code appelant. `_connect_autocommit` (DDL à chaud) est gardé pareil.

- **Site** = la coroutine `oto_mcp` la plus interne de la pile (`module::qualname`) : c'est
  elle qu'il faut décharger, quel que soit l'assistant qui touche la base. Une coroutine de
  test n'est pas un site ; un thread sans boucle (démarrage, `init_db`, timers, scripts) non plus.
- **Production** : un `logger.warning` par site et par process, `db.hors_boucle site=… ; pile :
  …`, jamais une exception (un site en défaut est une lenteur, la lever en ferait une panne).
  Aucune variable d'environnement, aucun schéma.
- **Tests** : `HorsBoucle` est levée pour tout site hors du **stock gelé**
  (`tests/_stock_db_hors_boucle.py`, posé par `tests/conftest.py`). La suite a très peu de base
  réelle : la levée rattrape ce qu'un banc à PostgreSQL ferait passer par un chemin indirect.

**Le cliquet statique** (`tests/_appels_db_hors_boucle.py` + `test_db_hors_boucle.py`) est
la moitié qui couvre la suite sans base : un graphe d'appels par nom / module importé /
`self.` / fermeture locale (les façades à ré-export plat `db` et `org_store` comprises)
liste tout `async def` qui atteint `_connect` par des appels **synchrones**. Il coupe aux
`await`, à `run_in_threadpool(f, …)` / `to_thread(f, …)` (`f` est passée, pas appelée) et
aux `lambda`. Le test échoue dans les DEUX sens : un site nouveau (il faut le décharger, pas
l'ajouter au stock), et un site du stock qui n'est plus fautif (il faut retirer sa ligne, sinon
le stock tolérerait sa régression). Ce qu'il ne voit pas — répartition dynamique, `getattr`,
callbacks — est le travail de la garde d'exécution ; ni l'un ni l'autre ne suffit seul.

**Ce que la mesure a trouvé de plus que les 71 appels directs du 21/09** : le balayage
direct s'arrêtait aux modules nommés `db.*`. En suivant les assistants, il y a **82 sites**
au moment de la pose — les façades `org_store` et `access` (`roles.*`, `current_org`,
`current_group`, `resolve_credential`…) cachent la base derrière un nom neutre, et 25 sites
(tous les middlewares MCP, les axes `_org`/`_project`, plusieurs outils) n'y arrivent que par
`current_user_sub_from_token`, qui ne lit la base **que tant que le drain d'alias est armé**
(`[dormant]` dans le stock : un interrupteur, pas un gel d'aujourd'hui).

### Lot 1 de décharge (21/09) : les routes sans jeton d'abord, puis les deux handlers les plus lourds

Demande d'oto cd : ce qui est atteignable **sans authentification** passe en premier, parce
que c'est ce qu'un tiers peut marteler. **82 → 62 sites** :

- `api/public.py` — toutes les routes publiques qui lisent la base (pages de partage
  `public_doc`/`public_doc_view`, désinscriptions `outreach_unsubscribe`/`digest_unsubscribe`,
  vitrines `guide(s)_library_public[_get]`, `invite_preview`, `connectors_catalog` — dont la
  branche anonyme). Les corps redeviennent des `def` synchrones sous un décorateur
  `api.base.en_thread` : l'objet reste un `async def` pour Starlette, pour `route.endpoint is …`
  et pour les bancs qui font `asyncio.run(route(req))`.
- le dispatch par Host, à CHAQUE requête : `subdomain_project.HostDispatch._http` et
  `subdomain_org.SubdomainOrgMiddleware` passent par `resolve_project_async` /
  `org_id_for_host_async`, qui ne paient le saut de thread que s'il y a de la base à lire (un
  host canonique n'a pas de slug ; un slug d'org en cache est servi de la mémoire).
- `_tls_check` (Caddy `ask`), l'annuaire public des projets MCP, la métadonnée de ressource
  protégée (`prm`, `valid_org_audience` seulement quand les deux crans gratuits n'ont pas
  tranché), le retour OAuth Salesforce (`callback` + `persist_token`, qui était un `async def`
  sans `await`).
- `runner.triggers` : le SQL de toutes les opérations (~40 appels) part au threadpool **en un
  bloc** (`_triggers_sync`) ; seuls les avertissements d'outils, asynchrones, restent dans la boucle.
- `me.credential.set` : lecture (`_set_preparer`) et écriture (`_set_ecrire`) au threadpool,
  la sonde du connecteur, asynchrone, entre les deux.
- `unipile_seats._list_seats` : `_platform_client()` (lecture du coffre) — le reliquat du
  correctif du matin, que la garde a trouvé.

Preuve : `tests/test_lot1_sql_hors_boucle.py` — compteur de boucle pendant une lecture de
0,5 s, **0 battement avant, ≥ 20 après**, sur neuf cas. **Reste 62 sites** dans
`tests/_stock_db_hors_boucle.py` (dont 25 `[dormant]`) ; les plus exposés qui restent :
`_IatGatedVerifier.verify_token` (audience d'un endpoint de projet), les outils
`tools/meta` (`oto_call`, `oto_list_my_tools`…), `api/media`, `api/projects`.
