---
title: Boucle d'usage (ADR 0017 — déroulés + feedback volontaire)
type: reference
description: >-
  Référence du flux d'événements de session unifié dans oto-backend (ADR 0017) :
  corrélation session_id + run_id sur tool_calls (colonnes OTO-locales, index dans
  ALTER pas dans _SCHEMA), tools spine run_start/run_finish (tools/doctrine_run.py,
  pile session FastMCP, runs imbriqués OK), et capacité feedback (signal
  tool_feedback|gap → table durable usage_signals hors prune 30j). Détaille les
  projections admin /api/admin/usage/* (runs, gaps, tool-quality, signals filtrables
  par les quatre états d'arbitrage) et l'arbitrage de signaux via oto_admin_signal
  (op=set_status). À charger
  pour comprendre comment tracer un déroulé d'agent, remonter un gap d'outil, ou
  déboguer un run_id manquant sur les appels.
adr:
  - "0017"
---

# Boucle d'usage (ADR 0017 — déroulés + feedback volontaire)

Un **flux d'événements de session** unifie le calllog (involontaire) + le feedback
volontaire d'agent + les runs / déroulés. Détail : ADR 0017 (repo public
`otomata-tech/oto`). Surfaces livrées (B1–B6) :

- **Corrélation** : `tool_calls` gagne 2 colonnes **OTO-LOCALES** (hors contrat canonique
  calllog/otomata-mcp) `session_id` (session mcp transport) + `run_id` (déroulé). Stampées
  par `server._calllog_sink` qui lit `get_context().session_id` + le run actif. ⚠️ piège
  rattrapé : l'index sur `run_id` va dans le bloc **ALTER** d'`init_db` (après l'ADD COLUMN),
  **jamais** dans `_SCHEMA` (no-op sur table existante → `UndefinedColumn` au boot).
- **Runs / déroulés** : tools spine `run_start`/`run_finish` (`tools/doctrine_run.py`) ;
  `run_start(label, doctrine?)` ouvre une doctrine nommée (`doctrine`=slug) **ou** un run
  one-shot (sans `doctrine`), même trace. Le `run_id` vit dans une **pile en état de
  session FastMCP** (`doctrine_run.py`, runs imbriqués OK), stampé sur chaque appel côté
  serveur — l'agent ne thread rien.
- **Signaux volontaires** : capacité MCP+REST unique (`capabilities/usage.py`) `feedback`
  — axe explicite `signal` ∈ `tool_feedback | gap` → table **durable** `usage_signals`
  (hors prune 30j). `gap` = cas d'usage non couvert (l'agent capte la demande non satisfaite).
- **Projections** (opérateur) : `/api/admin/usage/{runs,runs/{id},gaps,tool-quality,signals}`
  (`capabilities/usage.py`, PLATFORM_ADMIN) → vue dashboard `UsageView.vue` (« usage & déroulés »).
  `signals` filtrable par `status` ; la réponse porte AUSSI `counts` (la pile entière par
  état — une page ne dit pas si le stock fait 203 ou 2 000). Face MCP
  `oto_admin_signal(op='list')` (console ADR 0047).
- **Arbitrage — QUATRE états** (#450, 27/08) : `POST /api/admin/usage/signals/{id}/status`
  (MCP `oto_admin_signal(op='set_status')`, PLATFORM_ADMIN).
  - `open` — reçu, personne ne l'a regardé. Le retour à cet état EFFACE la trace
    d'arbitrage : un signal remis dans la pile n'a plus été arbitré, et garder l'ancienne
    note ferait lire une décision qui n'a plus cours.
  - `acknowledged` — lu, décision pas prise. **C'est l'état qui manquait**, et où vit
    l'essentiel d'une pile de retours.
  - `declined` — décidé de ne pas traiter. **`note` obligatoire** : sans motif, un refus
    est indistinguable d'un oubli.
  - `resolved` — traité. Aucune note exigée (le travail livré parle de lui-même).
  Le backlog vivant = `signals?status=pending` (= open ∪ acknowledged). `pending` est un
  FILTRE, pas un état.
- **Le retour à celui qui a signalé (#451, 27/08)** : `POST /api/admin/usage/notify-reporters`
  (MCP `oto_admin_signal(op='notify_preview'|'notify_send')`, PLATFORM_ADMIN). Colonne
  `usage_signals.notified_at` — NULL = son auteur ne sait pas encore ; effacée à chaque
  changement d'état, pour qu'un signal ré-arbitré soit re-annoncé.
  - **UN mail par PERSONNE, jamais un par signal.** Ce n'est pas une commodité : mesuré le
    27/08, 3 personnes portaient 168 des 204 signaux en attente, dont deux externes à 51 et
    53. Un envoi par signal aurait expédié cinquante mails d'affilée à un partenaire le jour
    où l'on vide la pile — la seule chose pire que le silence. Arbitrer 1 signal ⟹ un mail
    d'une ligne ; en arbitrer 53 ⟹ un mail de 53 lignes. Un seul chemin, les deux régimes,
    et le rattrapage d'une pile tombe du regroupement sans mode à part.
  - **`preview` est le défaut et ne touche à rien.** Ces mails partent chez des tiers sous
    notre marque : l'envoi est un ACTE (`notify_send`), et `only` permet de sortir par
    paliers. Deux `op` plutôt qu'un booléen `send=` — envoyer chez des tiers ne doit pas
    tenir à un drapeau qu'on oublie de mettre à False.
  - **Un envoi raté reste dû** : on marque APRÈS l'envoi, jamais avant. L'inverse ferait
    disparaître le retour au premier hoquet du mailer, sans que personne le sache. Une
    adresse inconnue (compte supprimé) se VOIT dans la réponse au lieu de se perdre.
  - **Seuls les états terminaux font un retour** : « on l'a lu » n'est pas une réponse, et
    l'annoncer userait le canal avant d'avoir rien dit.
  - ⚠️ **Le mail dit « vos agents », jamais « vous ».** Ces retours sont écrits par des
    agents en session sous le compte de quelqu'un qui n'a le plus souvent jamais su qu'ils
    existaient ; « votre signalement » lui attribuerait des mots qu'il n'a pas écrits.
    Et un `declined` s'écrit **« non retenu »** : le rapporteur a rendu service, on veut
    qu'il signale encore.
  - ⚠️ La marque est celle du DESTINATAIRE (`config.front_for`) — écrire « oto » à
    l'utilisateur d'un partenaire est un faux, même quand tout le reste est juste.
  - Les 331 signaux arbitrés AVANT ce lot sont marqués annoncés par la migration : leurs
    auteurs n'ont rien reçu, mais leur envoyer des nouvelles de décisions vieilles de deux
    mois n'aiderait personne. Le retour vaut pour ce qu'on arbitre à partir de là.

  ⚠️ **L'état se lit dans la colonne `status`, jamais dans `resolved_at IS NULL`.** Le trio
  `resolved_at/resolved_by/resolution` porte désormais le DERNIER ARBITRAGE, quel qu'il soit
  — un signal refusé porte lui aussi une date. Le dériver de la date rendrait un refus
  indistinguable d'un traitement, et le compteur mentirait dans le sens qui arrange.

  **Pourquoi ce lot** : deux états ne suffisaient pas, et la mesure du 27/08 le montre —
  534 signaux reçus depuis le 19/06, **203 ouverts dont 125 de plus d'une semaine**, et
  **zéro arbitrage depuis le 16/08** pendant que 118 arrivaient. Un stock où le refus est
  indicible ne peut que monter : on n'y distingue plus le retard du désaccord, donc on
  cesse de le lire — et la boucle d'usage devient muette tout en continuant d'enregistrer.
  (327 des 331 arbitrages passés sont d'une seule personne : la boucle s'arrête avec elle.)
- **Harnais impératif** : `_SERVER_INSTRUCTIONS` pousse l'agent à réflexer oto, encadrer
  par `run_start/finish` et émettre `feedback`.
- Déféré (otomata#32) : `why`-par-appel.

## Un run silencieux ne s'annonce plus « en cours » (13/08, #311)

> **Un run silencieux ne s'annonce plus « en cours » (13/08, #311).** 15 des 16 runs
> « ouverts » de prod n'avaient plus donné signe de vie depuis 1 jour à 1 mois : des
> conversations terminées sans clôture déclarée. Le silence est **dérivé à la lecture**
> (`run_status`, 48 h sans appel rattaché) — jamais stocké : une colonne d'état écrite par
> un démon pourrait mentir à son tour, ce qui est le défaut qu'on ferme. `last_seen_at`
> remonte de `_runs_from_journal`, donc les 4 lectures en héritent d'un coup.
> ⚠️ **Le vocabulaire d'issue vient de l'ADR, pas de la mesure** : `abandoned` est retiré
> (absent d'ADR 0058-D5) ; `failed` RESTE bien qu'il n'ait jamais servi non plus, parce que
> D5 le porte. **La mesure tranche ce que l'ADR laisse ouvert, jamais ce qu'elle a fermé.**

## Runs persistés (#50, amende le « state-only » d'ADR 0017)

> **Runs persistés (#50, amende le « state-only » d'ADR 0017).** La métadonnée
> sémantique d'un run (label / doctrine / outcome) vit désormais dans la table `runs`
> (`db.insert_run`/`finish_run`/`recent_runs`) — la pile session-scopée de
> `doctrine_run.py` reste la **source du run actif** (stampe `tool_calls.run_id`),
> `run_start`/`run_finish` y ajoutent la trace durable (best-effort, off-loop). Sert
> l'anticipation du contexte injecté (instructions bloc C) + la boucle d'usage dashboard.
