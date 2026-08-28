---
title: Monitoring & investigation des appels
type: reference
description: >-
  Référence du journal d'appels d'oto-backend : ToolCallLogger (oto_mcp/calllog.py)
  via hook on_call_tool, table tool_calls (kind mcp|rest|connector, corrélation
  session_id/run_id/org_id/client_id/sentry_event_id), prune au boot via
  OTO_MCP_CALL_LOG_RETENTION_DAYS (défaut 30j). Décrit les surfaces d'investigation —
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

**Extensions OTO-LOCALES** (hors contrat canonique, enrichies par le sink de
`server.py`) — ce sont les axes d'**investigation** :

| colonne | ce qu'elle répond | posé par |
|---|---|---|
| `session_id` | quelle conversation MCP | `ctx.session_id` |
| `run_id` | quel déroulé (`run_start`…`run_finish`) | jeton `_run_id=` puis pile `doctrine_run` |
| `org_id` | sous quelle org l'appel a été émis | seam `access.current_org` |
| `client_id` | depuis quelle surface (claude.ai, Claude Code…) | claim `azp` du JWT |
| `sentry_event_id` | où est le traceback | `SentryToolErrorMiddleware` |

⚠️ **Ces colonnes dépendent de l'ordre des middlewares.** `CallContextMiddleware` doit
rester le plus EXTERNE et `SentryToolErrorMiddleware` le plus INTERNE : sinon `_CALL_ORG`
est reset avant que le sink ne lise `current_org` (org d'audit fausse), ou l'event Sentry
n'est pas encore capturé quand la ligne s'écrit. fastmcp exécute les middlewares dans
l'**ordre d'ajout** (premier ajouté = plus externe). Contrat gardé par
`tests/middleware/test_middleware_order.py`.

## Ce qui n'est PAS tracé

Uniquement les **invocations d'outils** : pas la connexion d'un connecteur, pas le
`tools/list`, pas le handshake. Donc **compte actif ≠ usage** — un user avec un compte
(table `users`) mais 0 ligne `tool_calls` n'a jamais déclenché d'outil (connecté-mais-idle
OU handshake OAuth jamais réussi → diagnostiquer via `journalctl` 401). Vécu 2026-06-22.

`sentry_event_id` n'est posé que sur une erreur de **code** : une erreur GÉRÉE (4xx amont,
refus d'entrée) n'est pas capturée par Sentry (`before_send` la droppe), donc pas stampée.
Une ligne en erreur sans event id est donc normale — et informative : c'est un refus, pas
un bug.

Volumétrie bornée par un prune au boot (`prune_tool_calls` dans `init_db`, rétention
`OTO_MCP_CALL_LOG_RETENTION_DAYS`, défaut 30 j).

**Un déroulé s'efface entier** (#289) : le même prune retire, dans la même transaction,
les lignes `runs` qui viennent de perdre tous leurs faits. Un run *est* ses faits (ADR
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
| `calls` | le journal brut filtré | `tool`, `sub`, `errors`, `days`, `org_id`, `run_id`, `session_id`, `min_duration_ms`, `error_contains` |
| `call` | la fiche d'UN appel (args + corrélation) | `call_id` |
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
