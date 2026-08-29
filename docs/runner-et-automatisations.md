---
title: Runner hébergé & automatisations
type: reference
description: >-
  L'état du runner d'agents vit ici (fil des runs, file de jobs, déclencheurs), la boucle vi
  t dans `otomata-tech/oto-runner`. Plus le connecteur `routine` qui déclenche une routine C
  laude Code hébergée chez Anthropic.
---

# Runner hébergé & automatisations

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## Runner hébergé — l'état ici, la boucle dehors (chantier R1-R5, ADR 0064 au blueprint)

Le backend porte l'ÉTAT du runner d'agents hébergé ; la BOUCLE vit dans le repo
public **`otomata-tech/oto-runner`** (worker = client pur MCP+REST, ordonnanceur
de flotte `fleet.py` piloté par un YAML par campagne — AUCUN kind serveur, la
file reste uniforme ; déployé `/opt/oto-runner` sur otomata-0, gaté par le cran
`OTO_RUNNER_ARMED`). Trois tables + leurs capacités :
- **fil des runs** `run_messages` — capacité `runs.thread` (MCP `oto_run_thread`
  + REST `/api/me/runs/thread`) : état d'exécution EFFAÇABLE (purge 30 j), append
  = propriétaire seul, read = org_admin en projection neutre (`include_raw` au
  propriétaire) ; la reprise inter-agents lit le JOURNAL, jamais le fil.
- **file de jobs** `runner_jobs` — capacité `runner.jobs` (REST-only
  `/api/me/runner/jobs`) : claim SKIP LOCKED + bail re-claimable, backoff,
  `result` JSONB déclaré à la conclusion (usage_tokens, `tool_counts` — le
  « tour perdu », un agent qui analyse sans écrire, se lit au grain job),
  op=list org-scopé (surveillance dashboard `/automations`).
- **déclencheurs** `runner_triggers` — capacité + MCP `oto_trigger`, tick
  backend avec CAS sur `next_due` (prod/preprod partagent la base : un seul
  gagnant par échéance).
⚠️ Les jetons de contexte (`_project`…) sont advertisés PAR TOOL : un client
les pose d'après le schéma du tool, jamais à l'aveugle (un jeton non déclaré
fait refuser l'appel entier à la validation). Conception + état des preuves :
blueprint `chantier-runner.md` ; pilote = campagne Audiens (fusion R5, 14/08).

### `complete` libère les baux du run et rend le compte — `0` écrit (#633, 29/08/2026)

**Mesuré sur une campagne** : un poste de flotte lit « le témoin que la clôture du
travail rend » — or `op=complete` ne libérait aucune ligne du datastore et rendait
`{"ok", "status"}` sans compte. La libération ne jouait que sur `run_finish`, l'appel
de l'**agent** — qui rendait `rows_released` seulement s'il y avait au moins une ligne
(absent = zéro). Un agent mort sans `run_finish` laissait sa ligne au bail jusqu'à
expiration ; le **worker**, lui, survit à l'agent et conclut le job : c'est là que la
libération manquait.

**Depuis #633**, `complete` libère les baux du run que le job connaît — le `run_id` de
l'appel d'abord, sinon celui posé par `bind_run` (ou un `continue`) — par
`datastore_release_by_run`, **quel que soit `ok`** (un job qui repart en file avec
backoff ne travaille plus non plus ; la ligne revient dans la file, la reprise la
reprendra). Best-effort et HORS de la clôture, comme `run_finish` : le job est conclu
d'abord, la libération est un service rendu ensuite. La réponse porte trois champs
déclarés dans l'`Output` (donc dans l'OpenAPI) :

| forme | sens |
|---|---|
| `run_id: "…", rows_released: 2, release: "ok"` | le run tenait 2 lignes, rendues |
| `run_id: "…", rows_released: 0, release: "ok"` | le run ne tenait rien — **le 0 est écrit** |
| `run_id: null, rows_released: null, release: "no_run"` | aucun run connu du job : rien à libérer par run, rien n'est fabriqué |
| `run_id: "…", rows_released: null, release: "failed"` | la libération a échoué (journal serveur) ; le job est conclu, les baux expirent seuls |

`run_finish` écrit lui aussi `rows_released` **toujours** (`0` explicite ; `null` si la
libération a échoué) — sa description ne change pas, c'est la réponse. Preuves :
`tests/test_complete_releases_633.py` (chemin réel : réservation par le middleware +
`data_claim_next` monté, capacité `runner.jobs` telle que la route l'appelle, PostgreSQL)
et `tests/test_run_finish_releases_613.py`. ⚠️ `runner_jobs.run_id` référence `runs`
(FK) : un job ne se lie qu'à un run qu'un `run_start` a ouvert.

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
