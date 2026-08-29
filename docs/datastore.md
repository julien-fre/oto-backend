---
title: Datastore (spine natif PG, ADR 0016)
type: reference
description: >-
  Référence du spine de stockage structuré per-user de oto-backend : tables PG
  user_datastores + datastore_rows (JSONB natif, uuid7, _created/_updated_at auto),
  chargé hors gate d'activation (provider=None, ADR 0011), partage DB-only via
  datastore_shares, deep-link dashboard via data_url. Couvre les surfaces MCP data_*
  et REST /api/datastore/*, la file de travail (bail, plafond de reprises), l'auth double (JWT Logto ou API token oto_*), l'OAuth
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
pas une URL de Sheet. Code : `datastore/core.py` (`DatastorePg`) + `tools/datastore.py`
(face MCP) + `capabilities/datastore/*.py` (face REST, depuis #302 — plus
`api/datastore.py`, qui n'en porte plus rien) + fonctions `db.datastore_*`.

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
  des capacités `capabilities/datastore/{namespaces,rows,schema,sharing,claim,
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
chemin chaud) ; colle datastore dans `datastore/journal.py`. Lectures : capacités
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
(`capabilities/datastore/claim.py`, `mcp=None` assumé — `data_claim_next` tient la face
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
**forcée** (supervision dashboard), mais **refusée à un jeton porté** (`auth.token_scopes.
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

⚠️ **Correction datée (29/08/2026, #547) : la seconde source ne rend rien.** Le
paragraphe ci-dessus dit « les deux sources sont désormais lues une seule fois, au
middleware de contexte » — le jeton `_run_id=` explicite, puis la pile de session. Le
repli sur la pile est en fait **inerte** : `CallContextMiddleware.on_call_tool` appelle
`guide_run.active_run_id(context)` avec le `MiddlewareContext` de FastMCP, qui n'a pas
de `get_state` ; la lecture lève, est avalée, et rend une pile vide. Le test qui couvre
ce chemin passe un contexte de laboratoire qui, LUI, a `get_state` — le même piège que
celui dont son propre en-tête met en garde. Conséquence : **le datastore ne connaît un
run que si `_run_id=` est passé explicitement**, y compris dans une session serveur qui
tient un run actif. C'est ce qui rend le jeton obligatoire dans les faits, et ce que la
description de l'axe dit désormais (`call_axes.RUN`). Mesuré, non corrigé ici : le
correctif de la lecture est un lot à part, avec sa propre mesure.

⚠️ **Écrire un état terminal ne libère plus la ligne** — le store émet une notice à la
place. Un tableau dont le statut n'a aucun état terminal est une file qui ne libère
rien : `set_schema` le signale à la pose.

**Le plafond de reprises : distinguer « ça tourne » de « ça tourne à vide » (#433).**
Depuis que la ligne réservée est liée au run, la conclusion d'un traitement la libère —
c'est le design. Effet de bord mesuré au rodage d'une campagne : un agent qui réserve,
enquête, puis conclut SANS écrire rend sa ligne dans la minute, et le job suivant la
reprend pour refaire le même faux départ. **Deux lignes servies deux fois en dix
minutes, aucune écriture** — et rien qui le dise, puisque les jobs se terminent en
`done`. Un ordonnanceur de flotte ne peut pas borner ça par ligne : il ignore laquelle
l'agent a réservée. **Seul le serveur le sait.**

D'où un compteur porté par la LIGNE (colonne `datastore_rows.claims`, rendue `_claims`) :
il monte à chaque **prise** — `claim_next` comme `claim_row` — et **retombe à zéro à la
première écriture réussie**. Prendre, c'est acquérir une ligne libre ou dont le bail a
lâché ; le titulaire qui **renouvelle** son propre bail ne la prend pas (elle ne lui a
jamais échappé) et ne consomme donc rien : sur une file pilotée à la main, rafraîchir son
écran est le geste le plus banal, et le compter viderait le tableau de ses lignes. C'est cette remise à zéro qui
sépare « reprise après un vrai travail » de « faux départ répété » ; rien d'autre ne
les distingue de l'extérieur.

La garde est **OPT-IN et déclarée**, sur le cycle de vie du champ `role="status"` :

```
lifecycle: {
  states: ["a_traiter", "traite", "echec"],
  transitions: {"a_traiter": ["traite", "echec"], "echec": ["a_traiter"]},
  terminal: ["traite", "echec"],
  max_claims: 3,              # réservations SANS écriture tolérées
  abandon_state: "echec"      # DOIT être un état terminal déclaré
}
```

Les deux clés vont ensemble et se refusent à la pose : `max_claims` sans
`abandon_state` (garde qui ne pourrait pas s'appliquer), `abandon_state` non terminal
(la ligne reviendrait dans la file qu'elle vient de quitter), `max_claims` qui n'est pas
un entier ≥ 1. Ni l'une ni l'autre déclarée = **aucun plafond**, comportement historique.
`data_claim_next` accepte un `max_claims` qui SERRE la déclaration pour une passe (un
ordonnanceur peut être plus strict que le tableau) ; l'état d'abandon, lui, reste une
affaire de schéma.

Au-delà du plafond, le serveur verse la ligne dans `abandon_state`, pose le motif dans
une colonne de plateforme (`abandon_reason`, rendue `_abandon` : « abandonnée après 3
réservations sans écriture, plafond 3 » — le motif **cite ses chiffres**, le plafond
ayant pu changer depuis), libère le bail, et **journalise** (tableau, ligne, compteur).
Deux moments d'évaluation, et deux seulement :

- **au relâchement sans écriture** (`data_release`, et `run_finish` qui libère tout ce
  que le run tenait) — le cas nominal du faux départ ;
- **au claim**, en filet, avant de servir : c'est ce qui rattrape le bail expiré que
  personne n'a relâché (l'agent mort), sans quoi ce chemin contournerait le plafond.

⚠️ Une ligne sous bail **actif** n'est jamais abandonnée : son titulaire travaille
encore, et lui retirer la ligne serait la course que le bail existe pour empêcher.

Une ligne abandonnée **quitte la file quel que soit le filtre du client** : le pick de
`claim_next` exclut `abandon_reason IS NOT NULL`, filet de plateforme indépendant de ce
que l'appelant filtre. Elle reste lisible, et réparable : toute écriture réussie remet
le compteur à zéro ET efface le motif, donc la rouvre. ⚠️ Rouvrir son **statut** suppose
que le cycle de vie déclare la transition de retour (`"echec": ["a_traiter"]`) — la
plateforme verse la ligne dans l'état d'abandon, elle ne s'autorise pas à l'en sortir.

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

**Dire ce que cette version fait respecter (#389).** Quatrième signal du même jour sur
`data_set_schema`, et celui qui rendait les trois autres dangereux : il ne demandait pas
une contrainte de plus, mais de savoir lesquelles MORDENT. Le vrai sujet n'est pas le
vocabulaire, c'est le **décalage de déploiement** — `max_length: 60` posé sur quatre
colonnes d'un tableau de production, code de validation écrit le jour même, version
servie qui ne l'exécutait pas encore. Vérifié à l'époque : un PATCH idempotent rendait
200 ; avec le code à jour, **75 lignes sur 600** devenaient inécritables. Profil de
panne : effet DIFFÉRÉ au prochain déploiement, MASSIF et SIMULTANÉ, cause vieille de
plusieurs semaines — personne ne relie « les agents n'écrivent plus sur ces lignes » à
« quelqu'un a posé une borne un mardi », d'autant que l'erreur porte sur un champ que le
patch refusé ne touchait pas.

D'où **`enforced`** (`dsv2.enforced_keys()`), servi par les DEUX faces du schéma — à la
pose (`set_schema`, donc aussi `patch_schema`) et à la LECTURE (`data_get_schema`), sans
quoi il faudrait écrire un schéma pour poser une question. C'est une propriété du
SERVEUR, pas du schéma : rendue même quand rien n'est déclaré, parce que c'est au moment
où l'on s'apprête à déclarer qu'on veut la connaître.

⚠️ **Le relevé s'établit en FAISANT TOURNER le validateur**, jamais en recopiant une
liste : une sonde par clé = un schéma minimal + une ligne qui le viole, et la clé n'est
annoncée que si `validate_row` refuse ici et maintenant. Une liste parallèle divergerait
le jour où quelqu'un exécute une clé de plus (ou cesse d'en exécuter une) et se mettrait
à mentir dans les deux sens — exactement ce que le signal reproche au silence. Même parti
qu'`interpreted_keys` (dérivé du code), poussé d'un cran : dérivé du COMPORTEMENT, donc
insensible à la façon dont le code est écrit. Les clés dont l'effet est d'ARMER autre
chose portent en plus un **témoin** qui doit PASSER : `strict` n'interdit rien par
lui-même, et sans témoin on l'annoncerait dès que la conformité de type est vérifiée,
c'est-à-dire vrai par accident.

La moitié NÉGATIVE du signal — « `pattern` reçu : stocké mais non appliqué » — était
déjà servie depuis le 13/08 par `unknown_keys_warning` (#316, avec near-miss) et
`options_not_enforced_warning` (#319). `enforced` en est la moitié positive, la seule
qu'un client puisse vérifier contre le serveur qui lui répond.

**Une ligne créée sans la clé métier le DIT (#390, 3ᵉ demande).** Les deux premières
sont servies depuis le 13-15/08 : le bail protège l'ÉCRITURE et pas seulement
l'attribution (`_lease_guard` sous le verrou de ligne, `_assert_writable` sur les gestes
qui n'en ouvrent pas, titulaire reconnu par son RUN ou par `writing_as`), et l'adresse
égarée est traitée (`_id` dans `row` PROMU en adresse de fusion, un `id` nu non déclaré
REFUSÉ en nommant la ligne fantôme). Restait le cas sans adresse du tout : une insertion
franche sur un tableau dont le schéma déclare une clé métier, mais dont la ligne ne la
porte pas. Elle est légitime — un tableau se remplit souvent avant d'avoir sa clé — mais
aucune écriture ultérieure ne la retrouvera, et le lot qui dédouble passera à côté :
c'est la forme résiduelle de l'incident (une 501ᵉ ligne sans SIREN née avec tout
l'enrichissement, sans une erreur). D'où une `notice` sur `append_row`, pas un refus,
sans I/O supplémentaire (la clé est déjà résolue pour la dédup).
⚠️ **Mesuré avant de la poser** : 197 tableaux à clé métier déclarée, 50 024 lignes,
**3** sans clé. L'avertissement ne parlera quasiment jamais — c'est ce qui le rendra
lisible le jour où il parlera.
⚠️ **La 2ᵉ demande du signal — refuser une insertion sans `id` quand des baux sont actifs
sur le tableau — est écartée** : elle imposerait une lecture des baux à CHAQUE insertion,
sur le chemin chaud, pour couvrir un cas que les deux gardes d'adresse ferment déjà.

**`key_required` : un tableau où l'on ne crée pas, on VISE (#516, 29/08/2026).** Le
`notices` ci-dessus signale ; il ne refuse pas. **Un signal dans une réponse qu'un agent
ne consomme pas n'existe pas** — un refus nommé, lui, est lu par construction. D'où un
cran de schéma OPT-IN, `key_required: true`, à côté de la `key` qu'il durcit : sur un
tableau qui le porte, une écriture qui ne désigne **aucune ligne existante** — ni par son
identifiant (`data_write(id=…)`, ou l'`_id` promu depuis `row`), ni par une valeur de clé
que le tableau porte déjà — est **REFUSÉE** (`BusinessKeyRequired` → MCP INVALID_PARAMS,
REST `400 business_key_required`) au lieu de créer une ligne.

⚠️ **« Sans clé » couvre DEUX gestes, et le refus les distingue** — dire « clé requise »
à qui vient d'en fournir une le ferait chercher longtemps :
- **la clé n'est pas renseignée** — le cas du 28/08 : 8 911 lignes pour 8 910 sur un
  tableau de production, la ligne `01a04956-…` née sans `siren`, contenu bon, doublon
  parfait que rien ne rapprochera ;
- **la clé ne désigne aucune ligne** — le cas du 29/08, plus grave : deux agents refusés
  sur un identifiant INVENTÉ (deux conventions étrangères, aucune n'a la forme d'un `_id`
  d'ici) réécrivent sans identifiant avec un SIREN ; les deux SIREN sont inconnus **du
  registre**, deux lignes naissent, et les fiches affirment « registre — lu via fr_get »
  sur des entreprises qui n'existent pas. **Une clé n'empêche rien tant qu'elle peut être
  inconnue** : c'est cette porte-là que le cran ferme, et rien d'autre ne le pouvait
  (une garde de comptage côté runner ne voit qu'APRÈS).

**Le défaut ne bouge pas** : sans `key_required`, la création reste possible et reste
signalée par le `notices` de #390. Le cran est une déclaration du propriétaire du
tableau, jamais une politique de plateforme — un tableau se remplit souvent avant
d'avoir sa clé. Corollaire assumé : **un tableau fermé ne se peuple plus par écriture**,
`oto_upload_url` compris (il passe par le même `_write_rows_to_ns`) ; pour l'ouvrir, on
retire `key_required` du schéma. Il n'y a pas de paramètre d'échappement sur
`data_write` : un bouton « forcer » devient un réflexe et le cran redevient une
étiquette (même parti que l'absence de « forcer » sur le bail, #317).

Deux endroits, un seul par chemin d'écriture : `append_row` (ligne seule, face MCP ET
face REST) et `_write_rows_to_ns` (lot + upload signé) — le refus du lot NOMME la ligne
fautive et ce qui est déjà écrit, comme un refus de schéma (#412). ⚠️ Dans le lot, la
garde se juge sur la clé **déclarée** (celle qui porte l'index UNIQUE), même quand le lot
dédouble sur une autre via `key=` explicite : sinon un tableau fermé refuserait une ligne
qu'il porte déjà. `key_required` sans `key` se refuse **à la pose** (`validate_schema_def`),
et reste inerte s'il traîne dans un schéma déjà en base — un vieux schéma ne doit pas
rendre un tableau inécrivable. Il est annoncé par `enforced` (#389) via une sonde qui
interroge la fonction qui décide : il ne se prouve pas sur une ROW, puisqu'il se juge
contre le CONTENU du tableau.

**Contraindre la FORME d'une valeur (#387).** `field.pattern` — jumeau de
`field.max_length`, et il dit ce que la borne ne sait pas dire. Cas mesuré : un champ qui
doit porter une ÉNUMÉRATION de catégories séparées par des points-virgules, pas une
phrase de positionnement ; les longueurs des deux formes se recouvrent (20 à 207
caractères), donc borner à 150 tue les deux et borner à 250 n'attrape rien — **ce qui les
sépare est la structure**. Avant ce lot, `pattern` était accepté sans erreur et jamais
appliqué : le pire des deux mondes, puisque celui qui le pose croit avoir posé un contrat.
Le motif s'applique en `re.search` (donc il s'ancre lui-même : `^…$`), sur les seules clés
que le geste ÉCRIT — même restriction que la borne, et même raison : la validation portant
sur le mergé, une ligne déjà non conforme serait sinon inécritable pour n'importe quel
patch, y compris sur un champ sans rapport. Le refus cite la valeur CONSTATÉE et le motif
attendu. À la pose, `set_schema` avertit des lignes déjà hors motif
(`_offpattern_warning`), et ce verdict se calcule **en Python** sur les valeurs distinctes
rendues par `db.datastore_field_values` : le poser en SQL (`~` de PostgreSQL) ferait
compter par un moteur d'expressions et refuser par un autre, dont les dialectes divergent.

⚠️ **Une expression fournie par un appelant est une arme, et le serveur est mono-loop** :
un motif à explosion combinatoire n'y coûte pas une requête, il coûte le serveur entier —
même famille que la bombe de décompression (`docs/conventions.md`). Un garde purement
SYNTAXIQUE (« pas de groupe quantifié ») ne suffit pas, et c'est une mesure, pas une
intuition : sans un seul groupe ni une seule alternance, `.*.*.*.*.*z` sur 80 caractères
prend 0,75 s et `.*.*.*.*.*.*.*z` sur 60 caractères prend **14,8 s**. Ce qui explose est
le nombre de FAÇONS de découper le sujet. D'où un **budget** calculé sur l'arbre du motif
(`dsv2.pattern_refusal`) : le produit, quantificateur par quantificateur, du nombre de
longueurs qu'il peut prendre — une majoration de l'espace de recherche du moteur, plafonnée
à `PATTERN_BUDGET` (100 000). Il se calcule **contre la longueur du sujet**, ce qui rend
`max_length` OBLIGATOIRE sur un champ porteur de motif (≤ 1 000) : sans sujet borné il n'y
a pas de budget, donc pas de garantie. Sont refusés à la POSE, chacun en nommant sa raison :
la regex invalide, le motif > 200 caractères, le groupe ambigu répété (`(a+)+`, `(a|aa)*`),
la référence arrière, les assertions avant/arrière, et **toute construction que l'analyse
ne reconnaît pas** (fail-closed — un motif accepté par ignorance est exactement le défaut
à éviter). Conséquence assumée : le même motif est accepté sur un champ borné à 250 et
refusé sur un champ borné à 1 000, et une grammaire structurée (`^[^;]+(;[^;]+)*$`) est
refusée — l'interprétation métier d'une valeur n'est pas le métier d'oto.
⚠️ L'analyse emprunte le parseur de la stdlib, `re._parser` (3.11+) ou `sre_parse` (3.10,
**la version de la box**) : les deux chemins sont exercés au banc, et l'absence des deux
refuse tout motif plutôt que de laisser passer.
⚠️ **Aucun `pattern` n'existait en base au moment de la pose du garde** (inventaire du
28/08 sur les 210 schémas de production : 185 `max_length`, 0 `pattern`) — ce lot ne peut
donc geler aucune ligne existante. Un motif hérité qui ne passerait pas le garde reste
INERTE à l'écriture (`pattern_of` est muette, comme `max_length_of` sur une borne mal
formée) mais fait REFUSER la prochaine pose du schéma : c'est là qu'on peut encore corriger.

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

⚠️ **Et la POSE dit désormais ce qu'elle efface (28/08, remède A du même signal).** Le
geste qui ne peut pas détruire ne suffisait pas : `set_schema` reste la bonne façon de
POSER un format, et sa réponse ne disait rien de ce qu'elle emportait. Le point exact du
signal est que **le mode d'écriture était indétectable côté appelant** — la même session,
le même jour, sur le même tableau : sa migration a PRÉSERVÉ 78 notes de champ (elle
patchait le schéma relu en mémoire), son remappage en a DÉTRUIT deux (il rebâtissait la
liste), même méthode, même succès, réponse identique. Il fallait connaître son propre
code pour savoir ce qu'on venait de perdre, ce qui est hors de portée d'un agent qui
exécute une procédure écrite par un autre. C'est la forme exacte du défaut corrigé le
27/08 sur les LIGNES (`valeurs_effacees`), et il reçoit le même remède : toute réponse de
pose porte `declarations_effacees` + `declarations_effacees_hint`
(`dsv2.declarations_effacees`/`_report`). Trois natures dans un seul relevé — un champ
RETIRÉ (`retire: true`), les déclarations perdues sur un champ survivant (une note, une
borne, des options), et les clés de TÊTE (`key`, `strict` — ce qu'on perd en premier est
la clé métier et son index UNIQUE partiel). Le relevé descend dans les sous-records
(`contacts[].email`, même convention de chemin que `unknown_declaration_keys`) et ne
répète pas les enfants d'un champ déjà relevé comme retiré. **Les VALEURS y sont**, pas
seulement les noms : après la pose, la réponse en est la seule copie — bornes de rendu
20 entrées et 300 caractères par valeur, au-delà la TAILLE (projeter n'est pas tronquer).
Seules les DISPARITIONS comptent : réécrire une note est un geste qui se nomme lui-même.
⚠️ `patch_schema` passe son `remove` en `retraits_annonces` : le geste explicite ne crie
pas sur lui-même, mais **le filet reste tendu pour tout le reste** — c'est le seul moyen
de voir une fusion qui laisserait échapper quelque chose. ⚠️ Conséquence : `set_schema`
RELIT le schéma en place avant de le remplacer (un `SELECT` de plus sur un geste rare) —
un banc qui stubbe l'écriture doit désormais stubber aussi `_ns_of`.

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

**…mais DANS un composite déclaré, `strict` REFUSE (#544, 29/08/2026).** La liberté
qu'on protège au premier niveau n'existe pas un cran plus bas, et c'est toute la
différence : une clé inconnue en tête de ligne crée une vraie **colonne**, que
l'interface affiche et qu'on peut déclarer après coup — c'est ce qui permet d'explorer
un tableau avant de le typer. Dans un `object.fields` ou un `list.of.fields`, il n'y a
pas de « sous-colonne libre » : la déclaration EST le seul référentiel, l'attribut
serait stocké là où ni le schéma, ni l'interface, ni l'export à plat (§5.3 de
`datastore-colonne-tableau.md`, dont les colonnes se dérivent de `of.fields`) ne le
lisent. Sur un tableau `strict`, un attribut non déclaré est donc **refusé**, en
nommant l'élément — `contacts[1].email_pattern`.

*Le fait qui l'a montré* : un tableau `strict: true` a accepté deux fois, sur un rejeu
de nuit, une clé `email_pattern` **à l'intérieur** d'un contact — sans refus, sans
`hors_schema`, sans un mot ; la veille, le même geste au premier niveau avait été
interdit **par consigne**. « Une interdiction protège la forme qu'elle décrit ; le même
geste reparaît là où le texte ne regardait pas. » Le `strict` est précisément ce qui
doit rendre la prose inutile.

Quatre bornes, toutes voulues :

- **`strict` seul ferme** — un tableau non strict ne change pas de comportement, même
  quand la validation est armée par ailleurs (un `required` suffit à l'armer) ;
- **ce que le geste RÉÉCRIT seulement** — la fermeture ne descend que dans les
  composites nommés par l'écriture. Sans cette restriction, une ligne portant déjà un
  attribut hors format deviendrait inécritable pour n'importe quel patch, y compris sur
  un champ sans rapport : le gel de 23 lignes d'oto-backend#284, à ne pas rejouer ;
- **une liste dont le `of` ne déclare aucun champ reste LIBRE** — sans référentiel,
  rien n'est hors référentiel, et c'est la même règle qu'au premier niveau (un schéma
  strict sans aucun field ne relève rien) ;
- **une COUCHE n'est pas un attribut** — la forme servie d'un item aplatit ses couches
  (`email.origine`), donc un aller-retour lecture → écriture les repose telles quelles.

Le refus et le signal partagent **un seul prédicat** (`dsv2._unknown_subkeys`) : deux
définitions du « hors référentiel » finiraient par diverger, et c'est l'appelant qui
paierait la différence. Conséquence à connaître : sur un tableau `strict`, les chemins
**imbriqués** ne sortent plus dans `hors_schema` — le refus arrive avant le relevé.
`hors_schema` garde le premier niveau, qui est le seul endroit où la colonne libre est
un droit. ⚠️ Le refus est **borné comme le relevé** : un attribut inconnu est nommé une
fois par colonne-liste, sur le premier élément qui le porte — 300 contacts fautifs ne
rendent pas 300 lignes de refus.

**Ce que l'écriture VIDE se dit aussi (13/08, #407/#408/#409).** Ne pas nommer un champ
le laisse intact ; le nommer avec `null` l'EFFACE. Deux gestes différents, et le second
est indiscernable d'un `None` de sérialisation dans un payload — variable non peuplée,
gabarit à demi rempli, aller-retour de lecture. Toute réponse d'écriture porte donc
`valeurs_effacees` (`{ligne, champ, valeur}` — la valeur PERDUE, sans quoi il n'y a
rien à rétablir) + `valeurs_effacees_hint`, relevé dans les deux chemins qui fusionnent
(`update_row`, `_merge_into_row`) et servi par le même `off_schema_report()`. Bornes :
20 effacements nommés, une valeur rendue jusqu'à 300 caractères puis remplacée par sa
TAILLE. Le geste reste PERMIS — vider une valeur fausse n'a pas d'autre porte.
⚠️ **Erreur d'attribution à connaître** : trois signaux du 13/08 accusaient l'écriture
partielle d'avoir mis à `null` un champ *qu'elle ne nommait pas*. Le journal des appels
dit l'inverse (`tool_calls` 224531 puis 224704, même ligne) — huit minutes plus tôt, la
même session avait écrit `row={'moteur': None, …}` ligne par ligne. La règle du merge
tenait ; c'est le silence qui a fait chercher le défaut au mauvais endroit.

⚠️ **Une chaîne vide n'est PAS une valeur (28/08, #608) — et l'annoncer ne suffisait
pas.** Un client a perdu un signal de recrutement daté parce que son lot de sourcing
portait `best_signal: ""` dans son **gabarit** de ligne : un gabarit s'écrit une fois et
se réutilise sur toutes les lignes, donc un champ vide dans un gabarit était un vecteur
de perte à CHAQUE merge. La valeur n'a été rétablie que grâce à `valeurs_effacees`.
La cause tenait à une contradiction interne : `_is_empty` (le validateur) traite `""`,
`[]` et `{}` en **absence** — pas de contrôle de type, et « champ requis manquant » sur
un champ requis — pendant que `_merge_column` les traitait en **valeur** et les laissait
écraser. Tranché pour l'absence : **un vide non-`null` ne DÉPLACE jamais une valeur, il
ne peut s'écrire que là où il n'y a rien.** C'est ce que rend une source muette, pas une
demande d'effacement ; `null`, lui, ne se fabrique pas tout seul dans un gabarit.
La règle est volontairement étroite — là où la colonne était déjà vide, le geste passe
tel quel, donc **créer** une ligne depuis un gabarit ne change pas de comportement — et
elle se DIT : `valeurs_ignorees` + `valeurs_ignorees_hint`, clé distincte de
`valeurs_effacees` parce que les valeurs qu'elle nomme sont **encore en base**. Un
seul parcours (`datastore_columns.arbitrer_les_vides`) rend le payload corrigé et les
deux relevés : les faire diverger serait rejouer le défaut. Une écriture en couches qui
pose `{"valeur": "", "origine": …}` garde son origine — écarter la valeur vide n'emporte
pas ce qui l'accompagne (#326). Un refus dur a été écarté : 8 897 cellules à chaîne vide
sur 59 tableaux en production le 28/08, plus 5 643 listes vides sur 11 — les refuser
rétroactivement casserait des tableaux qui n'ont rien demandé.

**Batch write + clé métier (2026-07-03).** `data_write` accepte un LOT `rows` (list[dict])
écrit en un appel — importer un dataset sans faire transiter chaque ligne par le contexte
du LLM. Un namespace peut déclarer une **clé métier** au schéma (`schema.key`, ex.
`"email"`/`"siren"` ; cf. `data_set_schema`) : toute écriture qui porte cette clé fait alors
un **UPSERT (merge)** sur elle au lieu de dupliquer (param `key` explicite prioritaire) — les
rows sans clé sont appendées. Renvoie `{inserted, updated, count, key, ids}`.
⚠️ **La fusion par clé n'est PAS réservée au lot** (vérifié le 28/08 sur table jetable, et
la doc servie disait le contraire jusqu'au 29/08) : `data_write(row={siren: X})` **sans
`id`**, sur un tableau qui déclare `key: "siren"` et où X existe, met à jour la ligne
existante et rend son identifiant — `append_row` applique la même dédup que le batch
depuis #109 ch.3. Croire l'inverse fait écrire en lots de un pour obtenir une fusion, ou
pire, fait chercher un `id` qu'on n'a pas. Cœur : `store.write_rows` →
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
sous-champs sont vides. Vocabulaire FERMÉ, source unique dans `datastore/schema.py` :
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
| `db/rowabandon.py` | le plafond de reprises : quand la file cesse de tourner à vide |
| `db/datastore_ns.py` | le TABLEAU : existence, nom, propriété, partages |
| `db/datastore.py` | les LIGNES : CRUD + clé métier/index |
| `datastore/errors.py` | les refus — **aucune dépendance**, importable de partout |
| `datastore/columns.py` | la colonne côté Python : fusion des couches, résolution des anciens noms |
| `datastore/schema.py` | le FORMAT : le vocabulaire déclaré et sa validation |
| `datastore/schema_ops.py` | poser/retoucher/nettoyer le FORMAT (mixin du store) |
| `datastore/core.py` | le store qui COMPOSE — gros par nature |

Déplacements PURS : `db/datastore.py` et `datastore/core.py` ré-exportent, la surface plate
`db.<fn>` est figée par `tests/test_db_surface_frozen.py` (cliquet : on peut ajouter,
jamais retirer). ⚠️ Une scission fait dormir les noms hérités des globals dans les
branches rares — balayage figé par `tests/datastore/test_datastore_ns_duplicate.py`.

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
> écrites à la main d'`api/datastore.py` (10 chemins) sont des capacités
> (`capabilities/datastore/{namespaces,rows,schema,sharing}.py`, aux côtés de
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


## Provenance : une origine se CORRIGE, elle ne se supprime pas (28/08)

Deux règles, sorties d'un incident de mission (14/08 → 28/08) où une purge de couches
`origine` « hors vocabulaire » a été prise, quinze jours plus tard, pour la destruction
d'une pièce contractuelle — et où l'écran qui la restituait était resté vide sans que
personne le voie.

1. **Une couche `origine` se corrige, elle ne se purge pas — et jamais sur un critère
   générique.** Ce que `origine` doit porter est un contrat **par champ**, déclaré par le
   schéma ou la procédure : sur la plupart des champs, un NOM DE SOURCE (`client`,
   `registre`, `apollo`…) — une origine qui y recopie la valeur est hors vocabulaire et se
   **réécrit** vers le bon nom ; mais sur d'autres champs, `origine` **conserve la valeur
   d'entrée du client** (avant enrichissement ou réattribution), et « origine identique à
   la valeur » y est précisément le cas « retrouvée identique » que la restitution attend.
   ⚠️ **La liste des champs à entrée conservée est PROPRE À CHAQUE TABLEAU et se RELÈVE
   avant toute purge — jamais de mémoire, jamais d'un autre tableau** : sur le vivier du
   28/08 c'étaient `raison_sociale`, `nom_commercial` (743 + 383 « identiques ») et
   `charge_affaires` (79 origines = l'attribution du fichier client avant réattribution
   métier, écrites par la consigne en cours) — illustration du jour, pas définition. ⚠️ **« Purger là où l'origine
   égale la valeur » détruit donc exactement la mesure attendue** sur ces champs : une
   purge NOMME les champs où l'origine doit être une source, ne touche jamais ceux où elle
   conserve l'entrée, **commence par un EXTRAIT des valeurs supprimées** (namespace,
   row_id, chemin, valeur) déposé hors du tableau, et passe par l'outil (`data_write`,
   journalisé) — jamais par un SQL direct que le journal des appels ne voit pas. Le cas
   « retrouvée identique » se restitue aussi par comparaison avec la colonne `initial_of`
   déclarée au schéma.
2. **Une bascule de calcul se vérifie contre la donnée RÉELLE avant de servir.** Un
   consommateur qui passe « d'une colonne dédiée à la couche native » parce que c'est plus
   élégant vérifie d'abord que la couche est REMPLIE sur les lignes qu'il sert
   (`data_aggregate` sur le chemin, compte par ligne) — sinon l'écran est vide et le
   reste : personne ne regarde un écran vide. Même famille que « un filtre ignoré en
   silence » : tester par différentiel, pas à la lecture.

Corollaire de conversion : quand une colonne devient une colonne-liste
(`datastore-colonne-tableau.md`), la provenance des feuilles DOIT suivre (« la provenance
vit au grain feuille »). Sur le cas du 28/08 elle a suivi — mais parce que la procédure
portait déjà la source dans chaque élément (511 contacts sur 515), pas parce que la
conversion la garantit : vérifier après conversion que le compte des origines par feuille
égale celui d'avant, et ne pas lire l'absence d'une colonne SUPPRIMÉE par la conversion
comme une perte de provenance.
