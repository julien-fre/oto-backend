# Perf event-loop — le serveur est MONO-LOOP (les 2 modes de gel)

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
  un `client_factory` awaité par FastMCP (`mount.factory`) reste async — « pas d'await » ne
  suffit pas, vérifier que c'est un handler, pas un callback. Bornes connexions PG posées au
  passage (`db._connect_options` : `idle_in_transaction_session_timeout` anti-zombie-lock).
  **2ᵉ mode de gel identifié + corrigé (2026-07-02, py-spy en flagrant délit)** : du DB
  sync dans un MIDDLEWARE de la loop (`_authenticate`, gate ViewAs) × un blip de la RDB
  (SSL eof) → `pool.getconn()` attendait 30s en gelant le serveur ENTIER (2 downs).
  Protections : `ConnectionPool(timeout=5)` (`OTO_MCP_DB_POOL_TIMEOUT`) + chemin d'auth
  en `run_in_threadpool`. Observabilité posée : `loop_watch.py` (aiodebug — tout callback
  bloquant ≥1s est nommé au journal, ≥10s → event Sentry), py-spy sur la box
  (`py-spy dump --pid $(systemctl show -p MainPID --value oto-mcp)` PENDANT un gel),
  moniteur Kuma timeout 30s (timeout=0 = aveugle aux gels). RDB upgradée pico→nano.

## Le mode n°2 a une SECONDE porte : les middlewares MCP (incident du 15/08)

Même mode de gel (DB sync dans la boucle), autre famille de call-sites — et celle-là
passait sous les deux garde-fous. Sous la charge d'une campagne (~8 clients lourds :
3 workers runner, 4 agents locaux, un appariement qui écrit), 502 en rafale sur
`mcp.oto.cx` + 807 « Unexpected ASGI message after response already completed » en
20 min, **CPU calme** (load 0,31) — signature du gel de LOOP, pas de la saturation.

py-spy sur la box, MainThread, **3 relevés sur 6** (dont ≥4 s consécutives) :

```
psycopg execute ← has_credential (credentials_store.py) ← has_member_api_key
  ← walk_cascade (access.py) ← status_for ← _resolve_context (instructions.py)
  ← _c_layers ← session_layers ← compose_session
  ← on_initialize (middleware.py)          ← LE chemin async
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

D'où un garde-fou de nature différente, `tests/test_no_blocking_db_in_middleware.py` :
il n'analyse pas le source, il **observe le thread**. Le seam unique d'emprunt de
connexion (`db._conn._get_pool`) est remplacé par un mouchard qui note le thread
appelant puis refuse ; le chemin est vert ssi il a **réellement** tenté d'atteindre la
base (sinon la garde est inerte — le vert ne vaudrait rien) et qu'aucune tentative ne
vient du thread de la boucle. Profondeur quelconque, aucune whitelist. Un test de
contrôle vérifie que la garde MORD (le même travail appelé nûment dans la boucle est
bien attrapé).

⚠️ **Restent à traiter, MÊME classe, non couverts** (nommés plutôt que balayés en pleine
nuit d'incident) : `DynamicInstructionsMiddleware.on_list_tools` (org + index doctrine +
index guides, sync), `UserDisabledToolsMiddleware.on_initialize` →
`session_visibility.compute_hidden_tools` (`async def` qui fait 3 requêtes sync avant son
premier await), le combinateur d'autz `ORG_MEMBER` de `capabilities/_authz.py` appelé
depuis `_rest_adapter._handler` (`current_org` sync, relevé py-spy), et le sink du
calllog `server._calllog_sink` → `auth_hooks.current_user_sub_from_token` →
`db.upsert_user` (écriture + commit dans la boucle). Chacun est UNE requête ou trois,
là où la composition en faisait des dizaines — d'où l'ordre de traitement.

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

**La mécanique.** Un POST `/mcp` est en vol quand la session streamable-http se termine
(DELETE du client, ou stream fermé côté client). Le SDK MCP pousse alors dans un stream
mort (`writer.send` → `anyio.BrokenResourceError`) et le POST se conclut sur une réponse
ASGI **incomplète**, de deux façons :

- l'exception s'échappe — le `except Exception` du SDK renvoie un 500 **par-dessus le 202
  déjà envoyé**, uvicorn refuse (`RuntimeError: … after response already completed`) et
  la refuse jusqu'en haut ;
- ou l'app RETOURNE sans terminer le corps (branche SSE : le task group annule la tâche
  de réponse, le SDK avale) — uvicorn journalise « ASGI callable returned without
  completing response ».

Dans les deux cas uvicorn **ferme le transport**. Et c'est là qu'est le vrai dégât : Caddy
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
