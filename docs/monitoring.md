---
title: Monitoring & investigation des appels
type: reference
description: >-
  Référence du journal d'appels d'oto-backend : ToolCallLogger (oto_mcp/calllog.py)
  via hook on_call_tool, table tool_calls (kind mcp|rest|connector, corrélation
  session_id/run_id/org_id/client_id/sentry_event_id), rétention portée par le timer
  d'archivage (OTO_JOURNAL_RETENTION_DAYS, défaut 90 j) et non plus par un prune au
  boot — ADR 0065 lot 0. Décrit les surfaces d'investigation —
  console MCP oto_admin_monitoring + /platform/monitoring (plateforme),
  oto_org_monitoring + /org/monitoring (org_admin, mêmes lentilles bornées à SON org),
  /api/me/* (membre) — servies par les mêmes capacités. À consulter pour comprendre ce
  qui est tracé, enquêter sur une erreur, ou étendre la rétention.
---

# Monitoring & investigation des appels

## Ce qui est écrit

`ToolCallLogger` (`oto_mcp/calllog.py`, middleware inliné — ex-lib `otomata-calllog`
décommissionnée, contrat canonique dans le socle `otomata-mcp`) journalise **chaque**
appel de tool via le hook `on_call_tool` (point d'interception unique) dans la table
`tool_calls`. Best-effort : une erreur d'écriture du journal ne fait jamais échouer
l'appel ni n'avale l'exception métier, et l'INSERT part hors event loop
(`asyncio.to_thread`) — le chemin chaud de chaque appel ne doit pas attendre PG.

Colonnes canoniques : `server`, `kind`, `sub`, `email`, `tool`, `args` (**tronqués à
l'écriture**), `ok`, `error`, `duration_ms`, `created_at`.

`kind` discrimine l'événement (ADR 0017, « un seul flux ») : `mcp` = invocation d'outil
(défaut), `rest` = appel `/api/*`, `connector` = échec de résolution de credential.

### Ce que le journal ne porte JAMAIS : un jeton en clair

⚠️ **Corrigé le 2026-08-29 (#558) — le journal en portait.** La réduction de route
(`api/routes._normalize_route`) était une **allowlist de FORMES** : numérique ou UUID →
`:id`, tout le reste passe. Or quatre routes servies portent leur secret DANS le chemin
(`/api/upload/{token}`, `/api/public/docs/{token}`, `/api/invitations/{token}`,
`/api/invitations/code/{code}`) et aucun de ces secrets n'a la forme d'un identifiant.
Ils partaient donc en clair dans `tool_calls.tool`, sur toute la fenêtre de rétention,
relus par les trois étages de lentilles — **y compris le jeton d'invitation, que le
modèle de données refuse explicitement de persister ainsi** (`org_store/invitations.py`
n'enregistre que son empreinte). Un middleware transverse défaisait cette précaution.

La règle qui remplace la forme, source unique `oto_mcp/journal_secrets.py` :

- **par PROPRIÉTÉ, jamais par une liste de chemins** — un segment lié à un paramètre de
  route dont le NOM est déclaré secret (`token`, `code`) est réduit, quelle que soit son
  allure. La liste des routes concernées est **dérivée de la table servie**
  (`make_routes` appelle `declare_routes`) : une route future qui déclare `{token}` est
  couverte le jour où elle est montée, sans qu'on y pense ;
- **la route réduite ne porte pas le masque** — `tool` sert le `GROUP BY` du monitoring,
  une empreinte par jeton ferait exploser sa cardinalité. L'empreinte va dans `args`, où
  elle répond à « le même jeton a-t-il été rejoué ? » sans dire lequel ;
- **le masque est un HMAC clé (`#` + 12 hex), pas « les N derniers » ni un sha256 nu** —
  un code d'invitation fait 7 caractères sur un alphabet de 30 (~34 bits) : ses 8
  derniers caractères SONT le code entier, et un sha256 nu se casse par force brute en
  quelques secondes pour qui lit le journal. La clé est celle des jetons signés
  (`OTO_MCP_OAUTH_STATE_SECRET`), donc le masque reste stable d'un boot à l'autre ;
- **la même propriété sur l'autre face** — le jeton d'invitation arrive aussi par
  `oto_org op=accept_invite`. Un argument de **capacité** portant un de ces noms est
  masqué (`truncated_args(..., tool=)`), y compris via `oto_call` ; un argument de
  **connecteur** qui s'appelle pareil ne l'est pas (`droit_article(code='CT')` n'est pas
  un secret, et le cacher coûterait une lecture pour rien).

**Les lignes déjà écrites** se réparent à la main :
`oto-mcp maintenance journal-tokens` (§Rétention).

Cliquets : `tests/test_journal_secrets.py`, `tests/test_rest_call_logger.py`,
`tests/test_journal_no_plaintext_secret.py`, `tests/test_journal_token_purge_558.py`.

**Extensions OTO-LOCALES** (hors contrat canonique, enrichies par le sink de
`server.py`) — ce sont les axes d'**investigation** :

| colonne | ce qu'elle répond | posé par |
|---|---|---|
| `session_id` | quelle conversation MCP | `ctx.session_id` |
| `run_id` | quel déroulé (`run_start`…`run_finish`) | jeton `_run_id=` puis pile `guide_run` |
| `org_id` | sous quelle org l'appel a été émis | seam `access.current_org` — depuis le 30/08/2026 (#639), l'org du RUN quand l'appel porte `_run_id` sans `_org` |
| `client_id` | depuis quelle surface (claude.ai, Claude Code…) | claim `azp` du JWT |
| `sentry_event_id` | où est le traceback | `SentryToolErrorMiddleware` |

⚠️ **Ces colonnes dépendent de l'ordre des middlewares.** `CallContextMiddleware` doit
rester le plus EXTERNE et `SentryToolErrorMiddleware` le plus INTERNE : sinon `_CALL_ORG`
est reset avant que le sink ne lise `current_org` (org d'audit fausse), ou l'event Sentry
n'est pas encore capturé quand la ligne s'écrit. fastmcp exécute les middlewares dans
l'**ordre d'ajout** (premier ajouté = plus externe). Contrat gardé par
`tests/middleware/test_middleware_order.py`.

### Lire les arguments d'un appel : `args` sur la fiche, `arg_keys` sur la liste (#634, 2026-08-30)

Le journal **porte** les arguments (colonne `args`, tronqués et masqués comme ci-dessus).
Ce qui les rend, et sous quel nom, ne se devine pas — et s'est mal deviné le 29/08/2026 :
443 lectures de `GET /api/orgs/{id}/monitoring/calls/{call_id}` en douze minutes, conclues
« `arguments: {}` » sur des lignes dont `args` faisait 135 à 397 caractères. Rejoué sur la
route servie (adaptateur + PostgreSQL, `tests/test_journal_args_634.py`) : la fiche rendait
`call.args` plein. **Aucune vue n'a jamais émis de clé `arguments`** ; un lecteur qui la
cherche avec un défaut `{}` fabrique lui-même l'objet vide, et « je ne sais pas le lire »
s'est écrit « on ne peut pas savoir ». Depuis ce jour, le contrat le dit :

- **la fiche** (`op=call` sur les deux consoles, `GET …/calls/{call_id}` aux deux étages) :
  `call.args`, **tels que journalisés** — tronqués à l'écriture (300 caractères par
  valeur, valeurs composées stringifiées), jetons masqués (#582), `null` quand l'appel
  n'en portait aucun. Un seul chemin de lecture (`get_tool_call`) pour les trois faces ;
  le schéma de la 200 (`CallDetail`) le déclare ;
- **la liste** (`op=calls`, `GET …/calls`) ne porte pas le contenu — une page de 200
  lignes n'a pas à charrier 200 payloads — mais `arg_keys` : les **clés** des
  arguments, triées, `[]` sans argument. « Cet appel portait-il un numéro d'entreprise ? »
  se répond là, sans ouvrir une fiche, et sans qu'une valeur sorte (un secret masqué à
  l'écriture n'a jamais eu son NOM pour secret) ;
- **jamais un objet vide à la place d'une absence** : la liste n'a pas de champ `args`
  (la vue ne le porte pas), la fiche rend `null` (l'appel n'en avait pas) — les deux se
  lisent différemment, et c'est le but.

Ce que la fiche ne porte toujours pas : la **forme de la réponse** (vide / non vide /
refusée) — lot à part, même issue.

## Ce qui n'est PAS tracé

Pas la connexion d'un connecteur, pas le `tools/list`. (Ce paragraphe disait « uniquement
les invocations d'outils » jusqu'au 2026-08-29 : les appels `/api/*` y sont écrits depuis
`RestCallLogger`, et le handshake depuis `on_initialize` — c'est cet angle mort de lecture
qui a laissé passer #558.) Donc **compte actif ≠ usage** — un user avec un compte
(table `users`) mais 0 ligne `tool_calls` n'a jamais déclenché d'outil (connecté-mais-idle
OU handshake OAuth jamais réussi → diagnostiquer via `journalctl` 401). Vécu 2026-06-22.

`sentry_event_id` n'est posé que sur une erreur de **code** : une erreur GÉRÉE (4xx amont,
refus d'entrée) n'est pas capturée par Sentry (`before_send` la droppe), donc pas stampée.
Une ligne en erreur sans event id est donc normale — et informative : c'est un refus, pas
un bug.

Volumétrie bornée par le timer `oto-journal-archive` : il EXPORTE au froid S3 les mois
entiers au-delà de `OTO_JOURNAL_RETENTION_DAYS` (défaut **90 j**), puis les supprime.

⚠️ **Corrigé le 2026-08-28 (ADR 0065 lot 0, oto-backend#426), et il faut le savoir pour
lire un chiffre ancien** : jusque-là le boot purgeait `tool_calls` à **30 jours sans
archiver**, donc plus court que la politique écrite — et il vidait d'avance ce que
l'archive posée le 27/08 devait prendre (mesuré : 0 ligne au-delà de 30 j sur 969 314).
La rétention effective était d'un mois, personne ne l'avait décidé, et rien n'était parti
au froid. Depuis, elle a **un seul propriétaire**, l'archive. Le premier mois réellement
archivé sera août 2026, au tir du 2026-12-03.

**Un déroulé s'efface entier** (#289) : à la même borne, `oto-mcp maintenance retention`
(timer quotidien) retire les lignes `runs` qui viennent de perdre tous leurs faits. Un run *est* ses faits (ADR
0058-D2) et sa page est assemblée à la lecture — garder l'étiquette au-delà rendait, au
31ᵉ jour, une page VIDE sous une ligne qui annonçait « done ». Deux gardes : l'étiquette
d'un run **encore vivant** (ouvert il y a 40 jours, appelé hier) n'est jamais touchée, et
celle d'un run **récent** non plus, même si sa journalisation a échoué (best-effort).
Conséquence sur les lectures dérivées de `runs` (`project_runs`, `project_run_stats`,
pastille de procédure) : elles ne remontent pas au-delà de la fenêtre de rétention.

## Les deux surfaces (mêmes capacités, ADR 0009/0042)

Les lentilles vivent dans `capabilities/monitoring.py` — **un handler, deux faces**, autz
`PLATFORM_ADMIN` déclarée une fois. Ne pas rajouter de route écrite à la main ici.

**Face REST** (dashboard `/platform/monitoring`) :
`GET /api/admin/monitoring/{summary,rest,connectors,funnel,calls,calls/{id}}`.

**Face MCP** (agent) : console consolidée `oto_admin_monitoring(op=…)` — pattern ADR 0047,
un outil, verbe en `op` :

| op | pour | paramètres utiles |
|---|---|---|
| `summary` | agrégats (totaux, par outil avec avg+p95, par user, par jour) | `days`, `org_id`, `sub` |
| `calls` | le journal brut filtré — chaque ligne porte `arg_keys`, jamais `args` | `tool`, `sub`, `errors`, `days`, `org_id`, `run_id`, `session_id`, `min_duration_ms`, `error_contains` |
| `call` | la fiche d'UN appel (`call.args` tels que journalisés + corrélation) | `call_id` |
| `run` / `runs` | timeline d'un déroulé / déroulés récents | `run_id`, `limit` |
| `rest` / `connectors` / `funnel` | lentilles REST / santé connecteurs / activation | `days` |
| `gaps` / `tool_quality` | signaux d'usage agrégés | `days` |

`sub` accepte un **email OU un sub** (on enquête sur « les appels de jb@… », pas sur un
identifiant opaque). Les signaux bruts et leur résolution restent sur `oto_admin_signal`.

### Recette : enquêter sur une erreur

1. `op=summary` → quel outil concentre les échecs.
2. `op=calls` avec `tool=` + `errors=true` → les lignes fautives.
3. `op=call` sur une ligne → args, org, surface cliente, déroulé, **event Sentry**.
4. `op=calls` avec le `run_id` (ou `session_id`) de la fiche → ce qui s'est passé autour.

Pour un gel d'event loop : `op=calls` avec `min_duration_ms=5000` (cf.
`docs/event-loop-perf.md`). Pour la fenêtre longue, `days=` jusqu'à 365.

Côté dashboard, ces mêmes gestes sont l'onglet « journal » : filtres serveur, ligne
dépliable en fiche, axes de corrélation cliquables (ils refiltrent le journal), lien
Sentry quand l'event id est présent (gaté sur `VITE_SENTRY_ORG_URL` — sans lui, l'id est
rendu copiable plutôt qu'un lien cassé).

## Trois étages, un seul journal

La même table sert trois sièges, qui ne diffèrent que par le SCOPE — jamais par le
mécanisme, jamais par une projection dupliquée :

| étage | qui | ce qu'il voit | surface |
|---|---|---|---|
| membre | tout user | SON activité dans l'org active | `GET /api/me/{activity-summary,calls}` |
| **org** | **org_admin** | **tout ce qui a été émis SOUS son org** | **`oto_org_monitoring(op=…)` + `GET /api/orgs/{id}/monitoring/*`** |
| plateforme | platform_admin | tout | `oto_admin_monitoring(op=…)` + `/api/admin/monitoring/*` |

**Scope org = `tool_calls.org_id` / `usage_signals.org_id`, jamais l'appartenance du
membre.** Un membre de N orgs n'apporte à chaque étage que ce qu'il a fait sous celle-là,
donc les chiffres d'un écran org et ceux de l'export d'audit (#67) coïncident par
construction. ⚠ Les appels antérieurs à la colonne `org_id` (NULL) sont invisibles à
l'étage org — non reconstructibles.

L'étage org (`capabilities/org_monitoring.py`, autz `ORG_ADMIN_OF`) rejoue les lentilles
plateforme avec `org_id` posé, **plus une** qui n'existe qu'à cet étage, et **moins deux** :

| op | note |
|---|---|
| `summary` · `calls` · `call` · `runs` · `run` · `connectors` · `gaps` · `tool_quality` | mêmes projections, `org_id` passé |
| `adoption` | **propre à l'org** — membre par membre : qui s'en sert, qui n'a jamais essayé, qui est bloqué par un connecteur. Part d'`org_members` (sinon un membre à 0 appel serait invisible — c'est justement lui qu'on cherche) |
| `export` | rebranche `org.audit_log.export` (#67), même autz, même scope |
| ~~`rest`~~ · ~~`funnel`~~ | ne descendent pas : télémétrie de surface `/api/*` et comptes de toute la base = santé d'infra, pas usage d'org. `adoption` répond à la question du funnel à l'échelle d'une équipe |

**Gardes cross-org à ne pas perdre** : `call_id` est un BIGSERIAL donc devinable →
`op=call` compare `row.org_id` et rend le **même 404** qu'un id inexistant ; `op=run`
filtre en SQL puis 404 sur timeline vide. Testé par `tests/test_org_monitoring.py` — un
handler ajouté sans sa garde y casse.

**Le scope se DIT, et il compte ce qu'il laisse dehors (#630, 29/08).** Un `data_write`
refusé à 21:11:23 était dans `op=run` (les 17 appels du run) et absent de `op=calls
org_id=226` interrogé trois fois avec des motifs que son texte contenait — parce qu'il
avait été RÉSOLU sous l'org maison de l'appelant (axe `_org` absent, #631), donc stampé
`org_id=2`. La vue était exacte dans son périmètre ; le lecteur ne le connaissait pas, et
un « zéro » lu là était un plancher muet. `op=calls` scopé à une org (org ou plateforme
avec `org_id`) rend désormais, à côté des lignes : `scope` (la règle), `hors_scope` (les
appels des runs de l'org stampés sous une autre org, sous LES MÊMES filtres — même à 0)
et `hors_scope_hint` (où les voir : `op=run`). Fenêtre du plancher = `days`, sinon la
page quand elle est pleine, sinon 30 j — dite dans l'indice ; jamais sans borne
(28 ms/jour mesurés en prod). La construction des filtres est partagée
(`db/journal_calls.py`) : la page et son plancher ne peuvent pas diverger.
**Depuis le 30/08 (#639)**, la cause du cas mesuré n'existe plus : un appel sans `_org`
dans un run est résolu — donc stampé — dans l'org du run. `hors_scope` reste, pour ce
qu'un axe explicite continue légitimement de mettre dehors (agent multi-org).

## Rétention : 90 jours en ligne, le reste en froid (posé le 2026-08-27)

Le journal n'avait **aucune** rétention : 47 % de la base, et une croissance passée de
9 600 à ~90 000 lignes/jour en deux semaines sous la charge d'une campagne de runner.
Décidé par Alexis le 27/08 : **90 jours consultables**, au-delà chaque mois clos part en
CSV compressé sur l'Object Storage (`journal/tool_calls/YYYY-MM.csv.gz`, objet **privé**)
avant d'être effacé de la base. Travail mensuel `oto-journal-archive.timer` (le 3 à
04:45 UTC), script versionné `deploy/archive_tool_calls.py`.

**Ce n'est pas une purge de logs, et c'est le point à comprendre avant d'y toucher.**
Cette table est à double emploi : journal d'observabilité, ET **source de vérité des
exécutions** — un run n'est pas stocké, il est reconstruit depuis ses faits, qui sont
deux lignes d'ici (`run_start` / `run_finish`). Les effacer effacerait l'historique des
runs. Ils sont donc **exemptés de toute suppression** ; ils pèsent ~3 % du volume, les
garder indéfiniment ne coûte rien. ⚠️ Conséquence assumée : un run dont les appels
ordinaires ont été archivés garde son ouverture, sa clôture et son issue, mais son
« dernier signe de vie » retombe sur sa date d'ouverture (`last_seen_at` se dérive du
dernier appel rattaché) — sans effet sur un run clos, et un run resté ouvert depuis plus
de 90 jours est de toute façon lu comme silencieux.

**Trois précautions dans le script, chacune payée par une mesure du jour même :**
- **La suppression n'est autorisée que par une RELECTURE de l'archive** (téléchargée,
  décompressée, parsée, recomptée contre la base). Comparer la taille déposée à la taille
  locale prouve que l'upload n'a rien perdu — pas que l'export contenait tout, ni qu'il se
  relit. Sur une opération irréversible, la seule preuve est de refaire le chemin.
- **Le mois doit être ENTIÈREMENT sorti de la fenêtre.** Archiver un mois à cheval
  déposerait un fichier incomplet que la passe suivante ne compléterait pas (l'objet
  existe déjà) : des lignes effacées sans copie nulle part.
- **Ne jamais compter les enregistrements en comptant les sauts de ligne** du flux CSV :
  `args` et `error` en contiennent. Mesuré ici — 12 830 « lignes » annoncées pour 12 459
  enregistrements réels. Le seul compte juste est celui de la base.

**Où il tourne** : sur la box, en travail planifié, jamais dans le processus MCP —
mono-boucle, et c'est ce même journal qui l'a gelé le 27/08. Un verrou consultatif PG
protège de deux exécutions simultanées (prod et preprod partagent la base). Options :
`--dry-run` (dit ce qui partirait), `--export-only` (dépose et vérifie sans supprimer —
c'est ce qui permet d'éprouver le chemin réel sans engager la moitié irréversible),
`--retention-days N` (ou `OTO_JOURNAL_RETENTION_DAYS`).

⚠️ **La rétention à 90 jours n'effacera rien avant fin octobre 2026** : à sa mise en
place, le journal ne remontait qu'au 28/07. Un premier passage qui ne supprime rien est
le comportement attendu, pas une panne.

### Réparer les jetons déjà écrits (#558)

```bash
oto-mcp maintenance journal-tokens            # À BLANC : compte, n'écrit rien
oto-mcp maintenance journal-tokens --apply    # réécrit
```

**Une réparation, pas une suppression** : la ligne reste (qui, quand, quel code, quelle
durée), sa route est ramenée à la forme réduite et l'argument secret à son empreinte.
Ce qu'elle cherche est **dérivé de la même déclaration** que le masquage à l'écriture —
pas d'une seconde liste qui divergerait.

⚠️ **À blanc par défaut, hors timer et hors `all`** (comme `key-index-rebuild`, #421) :
elle réécrit des lignes servies aux lentilles de supervision, sur une base **partagée
prod/preprod**. La lancer est une décision, pas un effet de bord de sortie de maintenance.
Le piège qu'elle évite, et qui justifie son test contre un vrai PostgreSQL : la passe
générique `/api/invitations/` écraserait la route réduite par la passe spécifique
`/api/invitations/code/` si les préfixes plus spécifiques n'étaient pas exclus.

## Error tracking (Sentry)

Exceptions backend → **Sentry SaaS** (gaté `OTO_SENTRY_DSN`, no-op si absent →
le serveur boote sans). Deux captures : **500 des routes REST `/api/*`** via
l'intégration Starlette (auto) ; **exceptions des tools MCP** via
`SentryToolErrorMiddleware` (`sentry_setup.py`) — une erreur de tool est une erreur
JSON-RPC en **HTTP 200**, invisible à l'intégration Starlette, donc capturée là où
l'exception est vivante (vrai traceback, tag `mcp.tool` + `user.id=sub`). RGPD :
`send_default_pii=False` **et** `include_local_variables=False`. `before_send`
**droppe les 4xx amont** (`HTTP 4xx` d'une API tierce = input rejeté, pas un bug
backend). Env box : `OTO_SENTRY_{DSN,ENV,RELEASE,TRACES_SAMPLE_RATE}` ; région **EU**
`de.sentry.io` (org slug `otomata-vz`). Surveillance/triage = guide oto
`surveillance-erreurs` (token API en SOPS `sentry_api_token`).

⚠️ **`include_local_variables=False` n'est pas un doublon de `send_default_pii=False`, et
sans lui cette section était FAUSSE** (#564, corrigé le 2026-08-29). Elle affirmait
« jamais les args d'appel dans l'event » : `send_default_pii` ne couvre que ce que le SDK
collecte AUTOMATIQUEMENT (IP, cookies, en-têtes), pas le contenu des frames — et le défaut
du SDK pour les locales est `True`. Chaque exception repartait donc avec les variables
locales de toute la pile, dont celles du chemin de résolution de credential, qui tiennent
le secret **déchiffré**. Un réglage, pas un `before_send` qui scrube : une liste de noms à
scruber redevient fausse au premier renommage. Défense en profondeur dans le même geste :
le `repr` de `ResolvedCredential` et de `CascadeRung` est **expurgé** — c'est l'objet qui
voyage (frame, `logger.debug('%r')`, sérialisation d'un collecteur), pas la variable.
⚠️ **À vérifier hors dépôt** : les events déjà remontés chez le tiers sur la fenêtre de
rétention — le correctif ne les efface pas.
Un appel sur un tool HORS toolbox de session (la visibilité filtre `tools/list`,
pas `tools/call`) = erreur **GÉRÉE actionnable** `tool_not_mounted`
(`error_taxonomy` : oto_call immédiat / `oto_connector op=select`), droppée de
Sentry — plus jamais un « Erreur interne du serveur » opaque (vécu 16/07, #224/#225).
