---
title: Datastore (spine natif PG, ADR 0016)
type: reference
description: >-
  Référence du spine de stockage structuré per-user de oto-backend : tables PG
  user_datastores + datastore_rows (JSONB natif, uuid7, _created/_updated_at auto),
  chargé hors gate d'activation (provider=None, ADR 0011), partage DB-only via
  datastore_shares, deep-link dashboard via data_url. Couvre les surfaces MCP data_*
  et REST /api/datastore/*, l'auth double (JWT Logto ou API token oto_*), l'OAuth
  Google per-user multi-compte (flux /api/google/oauth/*, refresh token chiffré,
  scopes Sheets/Drive/Gmail/Tasks et gotcha CASA gmail.modify restricted), et la
  procédure de setup GCP one-shot. À consulter pour ajouter ou déboguer le datastore,
  configurer OAuth Google ou comprendre la séparation identité Logto vs délégation.
adr:
  - "0016"
  - "0011"
---

# Datastore (spine natif PG, ADR 0016)

Stockage structuré léger par user, **substrat PostgreSQL natif** (plus Google
Sheets — ADR 0016). Un namespace = une ligne `user_datastores` ; les rows vivent
dans `datastore_rows` (un dict **JSONB** par row, types préservés nativement,
fin de la sentinelle `__j:`). Schéma libre. Trois champs auto-managés exposés à
plat : `_id` (uuid7-like), `_created_at`, `_updated_at`.

**Datastore = spine plateforme** (`provider=None`, ADR 0011), PAS un connecteur
Google : chargé explicitement dans `register_all` (à côté de meta/orgs),
donc **hors gate d'activation** et **sans dépendance externe** — marche sans
connecter Google (plus de `412 google_not_connected`). Le partage est **DB-only**
(`datastore_shares` ; le destinataire lit via son propre `sub`, plus de
permission Drive). `data_url` renvoie un **deep-link dashboard** (`/console/data`),
pas une URL de Sheet. Code : `datastore.py` (`DatastorePg`) + `tools/datastore.py`
+ `api_routes_datastore.py` + fonctions `db.datastore_*`.

> **Export/sync vers un provider tiers** (Sheets/Docs/Notion — édition humaine,
> garantie de sortie) = projection optionnelle, **déférée à otomata#29**. C'est
> la raison d'être de l'unbundle, construite après.

> **Backfill** (Sheets → PG) : `scripts/migrate_datastore_to_pg.py` (idempotent,
> auto-suffisant pour la lecture Sheets). À lancer sur la box **après** le restart
> du code PG (brève fenêtre datastore-vide).

Surfaces :
- MCP tools `data_*` (`data_create_namespace`, `data_write`, `data_rows`,
  `data_delete_row`, `data_url`, `data_share`, etc.) — pour Claude.ai / Claude Code.
- MCP **App** `data_app` (`@mcp.tool(app=True)`, SEP-1865, prefab_ui) — variante à
  interface rendue : sans `namespace` = table des namespaces ; avec `namespace` =
  table triable/cherchable/paginée des rows, avec `filter` exact-match optionnel
  (même forme que `data_rows`) et `show_meta` pour les colonnes `_id/_created/_updated`.
  Rend le contenu INLINE dans le chat au lieu du seul deep-link `data_url`. Dégradation
  gracieuse si l'extra `fastmcp[apps]` est absent (non enregistré). Pattern : cf.
  `tools/foncier.py` (`foncier_*_app`).
- REST `/api/datastore/*` — pour le CLI `oto data` + UI dashboard.

> **Trier ET filtrer sur les dates système (05/08).** `order_by` acceptait déjà
> `_created_at`/`_updated_at`/`_id` ; le WHERE, lui, ne connaissait que
> `data ->> <champ>` — donc un filtre « modifiée depuis le 1er » cherchait la clé
> `_updated_at` DANS le JSON, ne la trouvait jamais et rendait **zéro ligne sans
> erreur**. `_ds_filter_clauses` route désormais ces trois noms vers leur vraie
> colonne (`_DS_META_TS_COLS`/`_DS_META_TEXT_COLS`), sur les deux faces (dashboard
> `filters=[…]`, agent `data_rows(filter={"_updated_at": {"gte": "2026-08-01"}})`).
> Deux règles à connaître : une valeur **date seule** désigne la **journée entière**
> (`lte "2026-08-05"` inclut le 5 — un `<=` nu comparerait à minuit et effacerait la
> journée saisie), et les ops sans objet sur une colonne NOT NULL (`empty`,
> `not_empty`, `contains`) sont **refusées** (400 nommant les ops valides) plutôt que
> servies vides. Une valeur de date malformée lève aussi côté Python : le cast SQL
> aurait rendu un 500 opaque au lieu d'un `invalid_filters`.

**Journal de travail : les deux surfaces, une seule table (2026-07-28).** Un geste fait
au cockpit (dashboard, REST) était journalisé au seul grain ROUTE (`RestCallLogger`,
`tool='PATCH /api/datastore/…'`) : on voyait qu'une écriture avait eu lieu, jamais
LAQUELLE ni depuis quel état — cliquer une transition de cycle de vie ne laissait donc
rien d'exploitable (ni retrouver la ligne, ni annuler). Les mutations REST posent
désormais AUSSI une ligne **sémantique** dans la même table `tool_calls`
(`kind='rest'`), nommée dans le **vocabulaire des tools MCP** (`data_write`,
`data_delete_row`, `data_release`) et portant `namespace`/`ns_id`/`id`/`fields`/
`from_status`/`to_status`. Helper unique `calllog.log_rest_call` (best-effort, hors
chemin chaud) ; colle datastore dans `datastore_journal.py`. Lectures : capacités
`me.datastore.row_activity` (`GET …/rows/{row_id}/activity`) et `me.datastore.activity`
(`GET …/activity`, `?limit=` borné 200) — elles ne filtrent plus `kind='mcp'`, et
résolvent `sub → email` **à la lecture** (un lot par page : `tool_calls.email` n'est
peuplé par aucun sink).

⚠️ **`from_status` vient de la MUTATION, pas d'une relecture.** Les mutations du store
(`update_row`/`delete_row`/`append_row`/`force_release`) acceptent un **relevé** `trace`
(dict mutable) qu'elles remplissent avec `ns_id`/`namespace`/`status_key`/`title_key`/
`prev_status` — pris là où ils sont déjà calculés. Le relire avant l'appel courrait avec
un write concurrent (un agent qui bouge la ligne entre les deux) et ferait proposer au
cockpit une annulation vers un état que la ligne n'a jamais eu ; ça ajoutait en prime
4 requêtes PG synchrones par mutation, sur un serveur mono-loop.

⚠️ **Le journal cite l'ENTITÉ, pas la chaîne tapée.** Le calllog journalise les args
BRUTS de l'appel — or `data_write` prend `namespace: str`, que l'agent remplit tantôt du
nom, tantôt de l'id, tantôt d'un `slot:<name>`. Corréler là-dessus obligeait à matcher
par NOM, avec trois dettes : un nom n'est unique que **par propriétaire**
(`uq_user_datastores_owner_ns`) donc il fallait le borner au tenant sous peine de fuite
cross-org, il change au **renommage** (historique orphelin), et un `slot:` n'est pas
rétro-résolvable. Depuis le 2026-07-28 les deux surfaces corrèlent sur le **`ns_id`
résolu serveur** : la face REST le tient de sa route, la face MCP du **relevé d'appel**
(`session_org.note_call_trace`, rempli par `DatastorePg._resolve` APRÈS les gardes —
un tableau refusé ne laisse pas de trace ; versé dans les args par `_calllog_sink`,
clés **fermées** `server._TRACED_ARGS`). Index d'expression partiel `idx_tool_calls_ns`.

> Le relevé est un **HOLDER MUTABLE** (dict posé vide par `CallContextMiddleware`), pas
> une valeur rebindée : les handlers de tools sont majoritairement des `def` sync
> dispatchés en threadpool, où `copy_context()` copie les BINDINGS — un `.set()` fait
> dans le thread ne remonte JAMAIS au contexte appelant, la mutation du dict posé en
> amont si (même objet). Garde-fou : `test_the_trace_survives_the_threadpool`.

L'axe NOM subsiste en **repli, borné au propriétaire** (`db._owner_clause` → `l.org_id`
ou `l.sub`), pour l'historique écrit avant cette bascule — il s'éteint de lui-même avec
la rétention 30 j. Même borne sur l'autre axe flou : la valeur de clé métier du parcours
d'une ligne (cherchée en sous-chaîne dans les args). Owner inconnu ou tableau d'équipe ⇒
l'axe flou est abandonné (sous-couvrir, jamais sur-matcher) ; `ns_id` et `row_id` (uuid4,
accès déjà prouvé) se matchent nus.

⚠️ **La lentille REST admin ne compte que les ROUTES.** `kind='rest'` porte maintenant
deux natures (route `MÉTHODE /chemin` de `RestCallLogger`, geste métier `data_write` du
journal) → `db.rest_call_stats` filtre sur la forme `position(' /' in tool) > 0`, sinon
chaque mutation du cockpit double-compte et `by_route` liste des pseudo-routes à latence
nulle. Les autres lentilles de monitoring filtrent `kind='mcp'` : elles sont intactes.

**File de travail : les deux surfaces RÉSERVENT (2026-08-08, signal #362).** Le bail
(ADR 0046 D, colonnes `claimed_by`/`claimed_until`) n'était posable que depuis le MCP
(`data_claim_next`) : une application web pouvait lire la file (`GET …/queue`) et
libérer, jamais réserver. Les fronts compensaient en écrivant un verrou **dans les
données** de la ligne — coopératif, donc non atomique (deux personnes qui cliquent à la
même seconde obtiennent la même ligne), et deux colonnes à prévoir par tableau pour une
mécanique déjà en base. Deux **capacités** REST-only comblent le trou
(`capabilities/datastore_claim.py`, `mcp=None` assumé — `data_claim_next` tient la face
agent) :

- `POST …/claim_next` `{worker, filter?, lease_s?}` → la prochaine ligne libre, réservée
  (`FOR UPDATE SKIP LOCKED`), ou `{row: null, hint}` quand il n'y a plus rien ;
- `POST …/rows/{row_id}/claim` `{worker, lease_s?}` → **cette** ligne. **409
  `row_claimed`** si un autre la tient (avec qui et jusqu'à quand) — un conflit se dit,
  il ne se devine pas. Renouvelable sans erreur par le **même** `worker` : rafraîchir son
  écran ne doit pas coûter sa ligne (`db.datastore_claim_row`, UPDATE conditionnel).

`worker` (libellé stable de celui qui réserve) est **exigé aux deux claims** : c'est la
garde rejouée au release. D'où le second cran, sur `POST …/rows/{row_id}/release` :
corps `{worker}` ⇒ libération **gardée** (`release_claim`) ; corps vide ⇒ libération
**forcée** (supervision dashboard), mais **refusée à un jeton porté** (`token_scopes.
current()` non None → 400 `worker_required`). Un jeton porté est le vecteur des
intégrations multi-utilisateurs : y laisser le forcé, c'est laisser chacun retirer la
ligne de son collègue. Côté portée, réserver **est une écriture** (`_ALLOWED` : les deux
claims en `WRITE`) — un jeton `read` lit la file sans pouvoir en retirer une ligne.

Refus de schéma : `ds_append`/`ds_update_row` traduisent `RowValidationError` en
**400 `row_invalid`** (détail = les champs/transitions fautifs), pas en 500 — c'est le
chemin d'échec d'une annulation (transition de retour devenue illégale).

**Batch write + clé métier (2026-07-03).** `data_write` accepte un LOT `rows` (list[dict])
écrit en un appel — importer un dataset sans faire transiter chaque ligne par le contexte
du LLM. Un namespace peut déclarer une **clé métier** au schéma (`schema.key`, ex.
`"email"`/`"siren"` ; cf. `data_set_schema`) : le batch fait alors un **UPSERT (merge)** sur
cette clé au lieu de dupliquer (param `key` explicite prioritaire) — les rows sans clé sont
appendées. Renvoie `{inserted, updated, count, key, ids}`. Cœur : `store.write_rows` →
`_write_rows_to_ns(ns_id, rows, key)` (keyé par ns_id → réutilisable **hors contexte d'org**)
+ `db.datastore_find_row_id_by_key` (lookup dédup JSONB paramétré). Pour du **volumineux**,
préférer `oto_upload_url(target='datastore')` (push NDJSON/CSV out-of-bande → même batch-upsert ;
ns_id scellé au mint, autz réappliquée via `ownership.can_access(datastore_namespace, write)`).
Cf. `docs/projects.md` §push out-of-bande (issue #105).

Auth :
- MCP tools : Logto JWT comme les autres tools.
- REST `/api/datastore/*` : Logto JWT **ou** API token long-lived (préfixe
  `oto_`, vérifié contre `user_api_tokens`).

OAuth Google per-user (Gmail + Tasks ; scopes Sheets/Drive latents pour l'export
#29 — **plus requis par le datastore**, ADR 0016 ; **multi-compte**) :
- `GET /api/google/oauth/start` (Logto auth) → renvoie `{auth_url}` à
  ouvrir dans le browser. `prompt=consent select_account` → l'user choisit
  quel compte Google connecter (rejouer le flow ajoute un 2e compte).
- `GET /api/google/oauth/callback?code=…&state=…` — Google redirige ici, on
  échange, dérive l'email du compte via le profil Gmail, persiste, puis
  redirige vers `app.oto.ninja/?datastore=connected`.
- `GET /api/google/oauth/status` → `{connected, accounts:[{email,is_default,scopes,granted_at}], …}`.
- `POST /api/google/oauth/default` body `{account}` → choisit le compte par défaut.
- `DELETE /api/google/oauth[?account=<email>]` → révoque un compte (ou tous).
- Scopes : `spreadsheets` + `drive.file` + `gmail.modify` + `tasks`.
- Multi-compte : dans le coffre `connector_credentials` (connector='google',
  `account=email`, `is_default` dans meta). Les tools `gmail_*`/`tasks_*`
  sans param `account` utilisent le compte par défaut (cf. `db.set_google_oauth`,
  `docs/connector-vault.md`).
- Refresh token **chiffré** (`secret_enc`) dans le coffre. access_token reste en
  clair dans `meta` (bearer ~1h, dérivé).

**Pourquoi un client OAuth séparé du connecteur Logto Google** : Logto
gère l'**identité** (scopes `openid email profile`), pas la délégation
d'accès aux ressources Google. Donc deux clients OAuth distincts dans le
même projet GCP — séparation propre identité ≠ délégation.

⚠️ **Conséquence de l'ajout de Gmail** : `gmail.modify` est un scope
**restricted** Google (contrairement à `drive.file`, non-sensible). Tant que
l'écran de consentement est en mode *Testing* (test users only), pas de
contrainte. S'il passe en *published/external*, Google impose un audit
sécurité annuel (CASA). Le flow étant unifié, **tout** user qui connecte
Google pour le datastore se voit aussi demander l'accès Gmail. Choix assumé
(substrat unique vs deux flows séparés).

## Setup GCP (one-shot, par projet)

1. **Console GCP** → choisir/créer un projet (peut être le même que celui
   qui héberge le connecteur Logto Google).
2. **APIs & Services → Library** : enable
   - `Google Sheets API`
   - `Google Drive API`
   - `Gmail API`
3. **APIs & Services → OAuth consent screen** :
   - User type : `External` (sauf Workspace)
   - App name : `Oto Datastore` (visible aux users sur le consent)
   - Support email : alexis@otomata.tech
   - Authorized domains : `oto.ninja`
   - **Scopes** : `.../auth/spreadsheets`, `.../auth/drive.file`,
     `.../auth/gmail.modify`, `.../auth/tasks`
   - **API à activer** : ajouter aussi `Google Tasks API` dans APIs & Services → Library
   - **Test users** (si en mode "Testing") : ajouter les emails autorisés
     tant que l'app n'est pas publiée. ⚠️ `gmail.modify` est un scope
     **restricted** → en mode Testing c'est OK, mais publier l'app en
     External imposerait un audit sécurité CASA annuel (cf. section OAuth
     ci-dessus). `drive.file` reste non-sensible ; c'est Gmail qui ajoute
     la contrainte.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** :
   - Application type : **Web application** (pas "Desktop")
   - Name : `oto-mcp datastore`
   - Authorized redirect URIs — le backend émet
     `{OTO_MCP_PUBLIC_URL}/api/google/oauth/callback` ; cette URL **exacte** doit
     figurer ici, sinon Google renvoie « requête invalide » (redirect_uri_mismatch).
     Depuis le cutover ADR 0040 (2026-07-06) le client est **partagé prod + preprod**,
     déclarer les deux :
     - `https://mcp.oto.cx/api/google/oauth/callback` (**PROD** — `mcp.oto.cx` depuis le cutover)
     - `https://mcp.oto.ninja/api/google/oauth/callback` (**PREPROD** — ex-prod avant le cutover)
     - `http://localhost:9103/api/google/oauth/callback` (dev, optionnel)
5. Copier `client_id` + `client_secret` → SOPS.
6. Générer le state secret :
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

## Env vars requises

À poser dans le `.env` systemd (ou SOPS exporté au boot) :

- `GOOGLE_DATASTORE_CLIENT_ID` / `GOOGLE_DATASTORE_CLIENT_SECRET` — issus
  de l'étape 5.
- `OTO_MCP_OAUTH_STATE_SECRET` — étape 6, HMAC anti-CSRF du state.
- `OTO_MCP_PUBLIC_URL` — déjà utilisée pour Logto (base du redirect URI).
- `OTO_APP_URL` (optionnel, défaut `https://app.oto.ninja`) — base où on
  redirige l'user après le callback OAuth. À override en dev local
  (`http://localhost:5174`).

Bootstrap d'un token CLI (pour Alexis) :
```bash
ssh -i ~/.ssh/alexis root@<box> \
  "cd /opt/oto-mcp && ./.venv/bin/python -m scripts.issue_token <SUB> cli"
# → imprime un `oto_…` à stocker dans SOPS comme OTO_API_KEY
```
