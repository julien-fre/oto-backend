---
title: Monitoring & investigation des appels
type: reference
description: >-
  Référence du journal d'appels d'oto-backend : ToolCallLogger (oto_mcp/calllog.py)
  via hook on_call_tool, table tool_calls (kind mcp|rest|connector, corrélation
  session_id/run_id/org_id/client_id/sentry_event_id), prune au boot via
  OTO_MCP_CALL_LOG_RETENTION_DAYS (défaut 30j). Décrit les DEUX surfaces
  d'investigation — console MCP oto_admin_monitoring (agent) et /platform/monitoring
  (dashboard) — servies par les mêmes capacités. À consulter pour comprendre ce qui
  est tracé, enquêter sur une erreur, ou étendre la rétention.
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
`tests/test_middleware_order.py`.

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
