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
