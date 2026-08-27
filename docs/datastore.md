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
(face MCP) + `capabilities/datastore_*.py` (face REST, depuis #302 — plus
`api_routes_datastore.py`, qui n'en porte plus rien) + fonctions `db.datastore_*`.

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
- REST `/api/datastore/*` — pour le CLI `oto data` + UI dashboard. **Face DÉRIVÉE
  depuis le 2026-08-12** (#302) : plus une seule route écrite à la main, tout vient
  des capacités `capabilities/datastore_{namespaces,rows,schema,sharing,claim,
  activity,columns}.py`. Conséquences pratiques : les 22 opérations portent leur
  schéma d'entrée ET de réponse dans `/api/openapi.json` (un intégrateur les génère),
  et un **champ inconnu est refusé** (400 `unknown_fields`) au lieu d'être ignoré —
  sauf le corps d'un ajout/patch de ligne, qui EST la donnée (`body_field`).
  ⚠️ Éditer un de ces chemins = éditer sa capacité ; en rajouter un à la main casse
  le garde-fou `tests/test_rest_modules_are_capabilities.py`.

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

**Le bail sait qui le tient, et il ne se lève plus tout seul (13/08, #317).** Trois
défauts constatés en PRODUCTION au premier essai réel, sur une campagne de 8 910
lignes. ① Le lien entre une ligne et le traitement en cours n'était jamais enregistré
(la source lue ne rend un run que s'il est passé explicitement, or un agent qui encadre
son travail empile dans l'état de session) : rien ne se libérait à la fin, et le
TITULAIRE lui-même se voyait refuser l'écriture. Les deux sources sont désormais lues
une seule fois, au middleware de contexte. ② Ce refus, non traduit, ressortait en
« erreur interne » : il est maintenant un refus NOMMÉ, portant qui tient la ligne,
jusqu'à quand, et comment la libérer. ③ La protection du chemin par LOT n'avait jamais
rien protégé — un fail-open sur les horodatages « rendus en texte », alors que le row
factory du dépôt normalise tout horodatage en texte : le cas cru marginal était le cas
normal. Les deux chemins avaient donc des comportements opposés, l'un refusant tout le
monde y compris le titulaire, l'autre ne refusant jamais personne. Une date illisible
REFUSE désormais au lieu d'ouvrir : un bail dont on ne sait pas s'il court protège
peut-être encore quelqu'un.

⚠️ **Écrire un état terminal ne libère plus la ligne** — le store émet une notice à la
place. Un tableau dont le statut n'a aucun état terminal est une file qui ne libère
rien : `set_schema` le signale à la pose.

Refus de schéma : `ds_append`/`ds_update_row` traduisent `RowValidationError` en
**400 `row_invalid`** (détail = les champs/transitions fautifs), pas en 500 — c'est le
chemin d'échec d'une annulation (transition de retour devenue illégale).

**Purger une colonne morte (#296 / signal #385).** Un schéma s'ajoute et se remplace,
il ne réduisait pas : retirer un champ le sortait de la vue, mais la clé restait dans
chaque ligne — rendue à la lecture, et acceptée en écriture. Après un renommage
(`actualite_sociale` → `analyse1`), l'ancien nom **décrit le contenu mieux que le
nouveau**, donc un agent qui relit une ligne écrit dedans en croyant viser juste (trois
fois de suite sur une mission, deux analyses sourcées perdues). Le geste manquant :
capacité **`me.datastore.drop_column`** (MCP `data_drop_column`, `rest=None` tant que le
cockpit ne l'affiche pas) → `db.datastore_drop_column` = `data = data - key`, l'opérateur
qui EFFACE là où écrire `null` conserve (une clé nulle reste une clé). Gardes dans le
STORE, donc valables pour toute face future : `confirm=True` obligatoire, refus d'une clé
**encore déclarée** au schéma (un `confirm` ne protège pas d'une faute de nom ;
l'échappatoire est le geste naturel du renommage — retirer le champ du schéma d'abord),
refus des colonnes de plateforme. En amont, `set_schema` **avertit** des colonnes
orphelines (`_orphan_columns_warning`, échantillon de 1000 lignes, strict seulement) : le
piège s'arme à la pose du schéma, c'est là qu'il faut le dire. ⚠️ **La purge n'est pas
sérialisée avec les écritures applicatives** : elle borne son UPDATE aux lignes portant la
clé (`WHERE data ? key` — les autres ne sont pas réécrites), mais un write concurrent fait
un read-merge-write du blob ENTIER (`_merge_into_row`, `SELECT FOR UPDATE` + UPDATE) — si
son SELECT précède la purge et son UPDATE la suit, la clé purgée **revient** sur cette
ligne. Fenêtre étroite et effet bénin (re-purgeable), mais réel : purger quand rien ne
draine le tableau, ou repasser après. Prendre le verrou de ligne dans la purge serait la
vraie réponse, et coûterait un parcours verrouillé de tout le namespace. ⚠️ **La 3ᵉ option du signal
— que `data_rows` cesse d'exposer les clés non déclarées en strict — est écartée** : elle
cacherait des données réelles, alors que le contrat 0016 promet qu'un champ libre
*s'affiche* et que #294 vient de trancher « signaler, jamais refuser ni masquer ». On
supprime la colonne ou on la déclare ; on ne la rend pas invisible.

**Retoucher un schéma sans le détruire (#388).** `data_set_schema` REMPLACE — bon geste
pour POSER un format, piège pour l'ÉDITER : deux appels indiscernables (même méthode,
même succès, même réponse) n'ont pas le même effet selon que l'appelant a patché en
mémoire ou reconstruit la liste des champs. Mesuré en une journée sur un même tableau :
un patch a préservé 78 notes de champ, une reconstruction a détruit un `pattern` et un
`max_length`, 52 notes ont disparu entre deux sessions. Un avertissement n'aurait rien
changé — personne ne lit un avertissement sur un appel qui réussit —, d'où un geste qui
ne PEUT pas détruire : capacité **`me.datastore.patch_schema`** (MCP `data_patch_schema`).
`fields` = **fusion par clé** (`dsv2.merge_fields`, récursive dans les composites
déclarés — patcher un sous-record ne détruit pas ses sous-champs ; l'ordre existant n'est
jamais rebrassé, il pilote le rendu) ; `remove` = le **retrait explicite**
(`dsv2.remove_fields`), pendant OBLIGÉ de la fusion — sans lui on troquerait la
destruction accidentelle contre l'impossibilité de nettoyer, et une clé inconnue y est
REFUSÉE (un `remove` avalé sur une faute de frappe ferait croire au nettoyage) ;
`strict`/`key` = les clés de tête, inchangées si omises. Le résultat repasse par
`store.set_schema`, donc par ses gardes (doublons de clé métier, index UNIQUE) et ses
trois avertissements — la logique n'est pas doublée. ⚠️ `remove` sort le champ du
**SCHÉMA** ; effacer la **COLONNE** des données reste `data_drop_column`.

**Écrire hors du format se DIT, sans être refusé (#294).** Sur un namespace `strict`,
un nom de champ que le schéma ne déclare pas est accepté (contrat 0016 : un champ libre
s'affiche, il ne débloque rien) et la valeur persiste — mais dans une colonne hors
format, que l'interface et tout ce qui s'appuie sur le schéma ignorent. Un humain relit
sa colonne et voit le vide ; un agent reçoit un accusé de réception et passe à la ligne.
Toute réponse d'écriture porte donc `hors_schema` + `hors_schema_hint`
(`dsv2.off_schema_keys`/`off_schema_warning`, relevé par le SEAM `DatastorePg._check_row`
— par lequel passent append/batch/merge/upsert/patch — et servi par
`store.off_schema_report()` aux **trois** surfaces : `data_write`, REST append/patch,
et la matérialisation d'upload signé). Le relevé porte sur les clés que le geste POSE
(pas sur le mergé, même raison que la borne `max_length` : une colonne hors format déjà
en base ne doit pas ré-alerter à chaque patch), il agrège un lot en une entrée par
chemin (`contacts[].tel`), et il est **vide hors strict** — là, le champ libre est un
droit explicite du contrat, pas une anomalie. Refuser franchement aurait été plus net,
mais aurait cassé cette liberté : ce qui manquait était un signal, pas une barrière.

**Ce que l'écriture VIDE se dit aussi (13/08, #407/#408/#409).** Ne pas nommer un champ
le laisse intact ; le nommer avec `null` (ou `""`) l'EFFACE. Deux gestes différents, et
le second est indiscernable d'un `None` de sérialisation dans un payload — variable non
peuplée, gabarit à demi rempli, aller-retour de lecture. Toute réponse d'écriture porte
donc `valeurs_effacees` (`{ligne, champ, valeur}` — la valeur PERDUE, sans quoi il n'y a
rien à rétablir) + `valeurs_effacees_hint`, relevé dans les deux chemins qui fusionnent
(`update_row`, `_merge_into_row`) et servi par le même `off_schema_report()`. Bornes :
20 effacements nommés, une valeur rendue jusqu'à 300 caractères puis remplacée par sa
TAILLE. Le geste reste PERMIS — vider une valeur fausse n'a pas d'autre porte.
⚠️ **Erreur d'attribution à connaître** : trois signaux du 13/08 accusaient l'écriture
partielle d'avoir mis à `null` un champ *qu'elle ne nommait pas*. Le journal des appels
dit l'inverse (`tool_calls` 224531 puis 224704, même ligne) — huit minutes plus tôt, la
même session avait écrit `row={'moteur': None, …}` ligne par ligne. La règle du merge
tenait ; c'est le silence qui a fait chercher le défaut au mauvais endroit.

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

⚠️ **Un lot N'EST PAS atomique, et son refus le dit (#412).** Il s'arrête à la première
ligne que le schéma refuse ; celles d'avant sont écrites et le RESTENT. Le refus nomme
donc la ligne autant que le champ — index dans le lot, valeur de la clé métier — et
combien de lignes ont atterri, parce que c'est ce qui décide de la reprise (rejouer le
lot entier re-fusionne les premières, ou les duplique sans clé métier). Vécu sur un
import de 8 910 lignes par lots de 200 : une adresse sans arobase dans le fichier
client, et le coût n'était pas les 199 lignes perdues avec elle mais le temps de la
retrouver. `DatastorePg._designation_de_lot` + `RowValidationError(row=…)` — le refus
garde sa CLASSE (les surfaces s'en servent pour choisir leur code), seule sa
désignation change.

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

## Sous-champs d'une colonne (#318, #322, #326)

**Toute colonne a des sous-champs** — ce n'est pas une forme que certaines valeurs
adoptent, c'est le contrat. Une colonne « plate » est simplement une colonne dont les
sous-champs sont vides. Vocabulaire FERMÉ, source unique dans `datastore_schema.py` :
`valeur` (la colonne elle-même) + trois couches, `origine` · `comment` · `link`.

**Le nom nu rend toujours la VALEUR.** `row["email"]` rend un e-mail, provenance ou
pas — sans quoi tout consommateur casserait, silencieusement, le jour où quelqu'un
pose une source. Les couches renseignées s'ajoutent à plat sous `champ.couche`, et
s'atteignent comme des colonnes : `{"field": "email.origine", "op": "empty"}` répond à
« quelles valeurs n'ont pas de provenance ? », qui est ce qui sépare une provenance
vérifiable d'une provenance décorative. Pas de `COALESCE` sur une couche : sur une
colonne scalaire elle est NULL, et c'est la bonne réponse.

**La table reste MIXTE pour toujours** (personne ne réécrira les lignes existantes) :
tout lecteur adressé par champ passe donc par `db.field_value_sql` /
`field_read_sql` — filtres, tri, agrégats, clé métier, contrôles de schéma — et aucun
ne recopie l'expression. L'index d'unicité de clé métier est un index d'EXPRESSION :
il doit matcher la chaîne du lookup au caractère près, d'où le littéral échappé plutôt
qu'un paramètre sur ce seul chemin.

**Écrire : l'écriture ne touche QUE ce qu'elle nomme** (`_merge_column`). Une règle,
dont découlent les deux défauts payés :

| écriture | effet |
|---|---|
| `{"champ": Y}` (ou `null`) | valeur posée/effacée, **origine intacte** |
| `{"champ": {"valeur": Y}}` | idem |
| `{"champ": {"origine": X}}` | **valeur intacte**, origine posée |
| `{"champ": {"origine": null}}` | origine effacée ; ne reste que la valeur ⇒ colonne à nouveau plate |

`comment` et `link` décrivent la valeur : quand elle change sans qu'ils soient
renommés, ils tombent avec elle — les garder ferait affirmer une provenance fausse.
`origine` décrit le point de départ, elle survit. C'est une protection contre
l'ACCIDENT, pas contre l'intention : un geste explicite remplace ce qu'il vise.

**Asymétrie lecteur/écrivain** : une couche inconnue est IGNORÉE à la lecture (un
déploiement progressif ne doit pas perdre ce qu'un nœud plus récent a écrit) et
REFUSÉE par son nom à l'écriture (une couche mal orthographiée s'apprend tout de
suite, pas six semaines plus tard). ⚠️ Un dict qui mêle une couche connue et une clé
inconnue reste une donnée `json` métier — arbitré en #329.

**Le blob lu en TEXTE** (recherche plein-texte, extrait, embedding) est reconstruit
avec les valeurs à la place des enveloppes (`ROW_VALUES_TEXT_SQL`), sinon `q=hunter`
matcherait toute ligne dont l'e-mail VIENT de Hunter. Gardé par un `jsonb_path_exists`
mesuré : ×6,4 si systématique, ×1,5 sur une table sans couches.

## Interroger PLUSIEURS colonnes à la fois (oto#22 barreau 1)

Une notion vit souvent sur des colonnes numérotées (`contact1_fonction`…). Un filtre
peut viser plusieurs colonnes **déclarées par l'appelant** — le serveur n'interprète
jamais un motif de nom :

```jsonc
filters: [{"fields": ["contact1_fonction","contact2_fonction","contact3_fonction"],
           "op": "in", "value": ["DRH","DAF"], "match": "any"}]
```

`match` : `any` (défaut, une colonne suffit) ou `all` — et `all` n'est pas la négation
d'`any` : « aucun rang n'a de contact » (`empty` + `all`) ne s'obtient pas en niant
« au moins un rang en a ». Une métrique d'agrégat porte sa propre condition (`where`,
même grammaire) : le total et la sous-population dans la MÊME requête, donc un taux
sans recouper deux appels. `group_by` accepte une liste — les valeurs sont mises en
commun, `count` compte les occurrences et `count_rows` les fiches.

Surfaces : `data_rows(filters=…)`, `data_aggregate(filters=…, metrics=[{…, "where":…}],
group_by=[…])`, et le même `filters` côté REST.

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

## Découpé par COUTURES depuis le 13/08 (#325)

**Découpé par COUTURES depuis le 13/08 (#325)** — le fichier est l'unité d'occupation
d'une session sur un tree partagé, et quatre chantiers ont dû entrer dans les trois
mêmes fichiers en une semaine (gels en série, un incident de tree). Où poser un lot :

| module | ce qu'il porte |
|---|---|
| `db/paths.py` | désigner une valeur : `email` · `email.origine` · `contacts[0].email` · `contacts[].email` |
| `db/query.py` | construire filtres/tris/agrégats — **PUR**, ne touche jamais une connexion |
| `db/rowlock.py` | le bail d'une ligne (file de travail) |
| `db/datastore_ns.py` | le TABLEAU : existence, nom, propriété, partages |
| `db/datastore.py` | les LIGNES : CRUD + clé métier/index |
| `datastore_errors.py` | les refus — **aucune dépendance**, importable de partout |
| `datastore_columns.py` | la colonne côté Python : fusion des couches, résolution des anciens noms |
| `datastore_schema_ops.py` | poser/retoucher/nettoyer le FORMAT (mixin du store) |
| `datastore.py` | le store qui COMPOSE — gros par nature |

Déplacements PURS : `db/datastore.py` et `datastore.py` ré-exportent, la surface plate
`db.<fn>` est figée par `tests/test_db_surface_frozen.py` (cliquet : on peut ajouter,
jamais retirer). ⚠️ Une scission fait dormir les noms hérités des globals dans les
branches rares — balayage figé par `tests/test_datastore_ns_duplicate.py`.

## Ce qu'oto SAIT d'un champ, et ce qu'il ne saura jamais (14/08)

⚠️ **Ce qu'oto SAIT d'un champ, et ce qu'il ne saura jamais** (tranché par Alexis le
14/08). Oto gère les **types standards** : un `number` se trie numériquement, une date
chronologiquement — l'ignorer donnait `10, 100, 2, 9` (livré v1.112.0). Il ne gère PAS
l'interprétation métier d'une VALEUR : que `20_49` soit une tranche INSEE qui suit
`1_2` est le savoir du consommateur, jamais celui d'oto. Entre les deux, l'ordre des
`options` déclarées au schéma **est honoré** — parce que c'est une DEMANDE adressée à
oto, pas une compréhension qu'il aurait du métier. Même frontière que `flat_alias` :
exécuter une déclaration n'est pas deviner une convention.

## La face REST est 100 % DÉRIVÉE depuis le 2026-08-12 (#302)

> **La face REST est 100 % DÉRIVÉE depuis le 2026-08-12 (#302)** : les 17 routes
> écrites à la main d'`api_routes_datastore.py` (10 chemins) sont des capacités
> (`capabilities/datastore_{namespaces,rows,schema,sharing}.py`, aux côtés de
> `claim`/`activity`/`columns` déjà migrés) — mêmes chemins, mêmes réponses, **mêmes
> codes** (201 sur les créations), mais entrée et sortie déclarées : les 22 opérations
> datastore de `/api/openapi.json` portent désormais un schéma de réponse, contre 5
> avant. `mcp=None` partout : les tools `data_*` sont inchangés, ce lot n'a migré que
> le REST. Trois crans ont été ajoutés au moule pour que ce soit possible sans casser
> le fil (`RestBinding.status`/`body_field`/`reads_body`, cf. §Couche capacité).
> ⚠️ Le refus de champ inconnu s'applique donc maintenant à ces chemins : `oto data
> list --filter k:v` (oto-cli) envoie un `filter` que la route ignorait en silence
> depuis le passage à `page_rows` — il rend désormais 400. Le paramètre est mort côté
> serveur, pas côté client.

## `data_write` — deux sémantiques à connaître (sorties de la description de l'outil le 27/08)

Ces deux paragraphes vivaient dans la description de `data_write` servie au modèle (ajoutés entre v1.148 et v1.151). Ils en sont retirés le 27/08 pour un essai A/B : la fréquence des appels d'outil malformés d'une campagne est passée de 21 % à 62 % sur la vague lancée juste après v1.151.0, et **la longueur des descriptions d'outils est le seul changement sur le chemin de cette campagne** (sensibilité à la longueur d'instruction mesurée le 15/08). Le comportement, lui, est inchangé et reste servi par les réponses de l'outil (`valeurs_effacees`, refus nommant la ligne).

- **Ne pas nommer un champ le laisse intact ; le nommer avec `null` (ou `""`) l'EFFACE.** Deux gestes différents — un `null` glissé dans un payload (variable non remplie, gabarit à moitié rempli) détruit la valeur en place. Les effacements reviennent dans `valeurs_effacees` (champ, ligne, valeur PERDUE) pour réécrire ce qu'on n'a pas voulu vider.
- **Un LOT n'est pas atomique** : il s'arrête à la première ligne que le schéma refuse, et les lignes d'AVANT restent écrites. Le refus nomme cette ligne (son index dans le lot, sa clé métier quand il y en a une) et dit combien de lignes ont atterri — reprendre de là plutôt que rejouer le lot entier.

Si l'essai montre que la longueur ne pèse pas, les deux paragraphes reviennent dans la description (ils y sont utiles) ; s'il montre qu'elle pèse, la règle devient : **les descriptions d'outils portent le contrat minimal, le détail vit dans les réponses et les guides**.

