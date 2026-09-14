---
title: Faire tourner une procédure en autonome (routine Claude Code)
description: "Monter une routine Claude Code qui exécute une procédure oto sans personne devant l'écran : le prompt est un POINTEUR vers la procédure (jamais une copie), le connecteur Oto porte les outils, et le déclencheur peut être planifié, API (instantané) ou GitHub. Couvre le gabarit de prompt, le passage de contexte à un run (donnée non fiable, passer une référence), et les cinq causes d'échec — secrets restés sur le poste en tête."
---

# Faire tourner une procédure en autonome

Une **routine Claude Code** est un agent hébergé par Anthropic : un prompt figé, des connecteurs MCP, un ou plusieurs déclencheurs. Y brancher le connecteur Oto et lui donner un prompt qui *charge une procédure*, c'est faire tourner cette procédure sans machine allumée et sans personne devant l'écran.

Le montage tient en une phrase : **le prompt de la routine est un pointeur, jamais une copie.** Le savoir-faire reste dans la procédure — versionnée, éditable, partageable, lue à chaque run. Le prompt fait trois lignes et ne bouge plus.

Recopier la procédure dans le prompt est l'erreur qui coûte le plus cher : deux sources de vérité qui divergent en silence, et une correction de doctrine qui n'atteint jamais l'agent.

## Monter la routine

Sur `claude.ai/code/routines`, **Nouvelle routine** :

1. **Nom** — ce que fait la routine, pas la procédure qu'elle charge.
2. **Instructions** — le pointeur (gabarit ci-dessous).
3. **Dépôt** — aucun, sauf si la procédure touche du code. Une routine sans dépôt démarre sur un contexte nu, ce qui est exactement ce qu'on veut : elle ne connaît que la procédure et les outils.
4. **Connecteurs** — **ne garder que ceux dont la procédure a besoin.** Tous les connecteurs du compte sont ajoutés par défaut, et pendant un run l'agent peut appeler n'importe lequel de leurs outils, écritures comprises, sans confirmation.
5. **Déclencheur** — voir plus bas.

## Le gabarit de prompt

```
Charge la procédure `<slug>` : oto_procedure(op="get", slug="<slug>").
Elle porte la méthode complète et fait autorité : en cas de divergence avec
ce prompt, c'est elle qui gagne.

<Le périmètre du run : sur quoi l'appliquer, ce qui compte comme terminé.>

Tu tournes sans dépôt et sans machine locale : tout passe par les outils du
connecteur Oto (préfixe `mcp__Oto__`). Ils ne sont pas tous chargés d'emblée
— résous ceux dont tu as besoin avec ToolSearch.

Si la procédure est introuvable, si la source est vide, ou si un outil te
manque, dis-le explicitement dans ton compte rendu au lieu de produire un
résultat plausible.
```

La dernière ligne n'est pas de la politesse : un agent autonome que personne ne relance a une pente naturelle à combler les trous. Lui demander de nommer ce qui a bloqué transforme un run raté en information exploitable.

## Les trois déclencheurs

Ils se combinent sur la même routine.

| | quand | à savoir |
|---|---|---|
| **planifié** | cadence régulière ou date unique | cron en **UTC**, intervalle **minimum une heure**. Un tir unique ne compte pas dans le plafond quotidien de runs |
| **API** | déclenchement instantané par un tiers | endpoint `/fire` + jeton bearer. **Le jeton se crée uniquement dans l'interface** et ne s'affiche qu'une fois — aucune API publique ne le génère |
| **GitHub** | pull requests, releases | filtres par auteur, branche, labels, état |

Le déclencheur API est le seul chemin temps réel. Le minimum d'une heure ne concerne que le planifié.

## Passer du contexte à un run

Le champ `text` du déclenchement arrive à l'agent **enveloppé dans un bloc `<routine-fire-payload>` étiqueté donnée non fiable**, avec la consigne de ne pas suivre les instructions qu'il contient. Le prompt de la routine doit explicitement opter pour le lire, sinon le texte reste du contexte inerte.

Conséquence directe : **passer une référence, jamais l'enregistrement.** « la ligne `019ff08f…` du tableau leads » plutôt que le lead recopié — l'agent recharge la donnée fraîche par oto, et le payload ne devient jamais un vecteur d'instruction. Plafond : 65 536 caractères, ce qui est une autre façon de dire la même chose.

## Ce qui casse un run, par fréquence décroissante

1. **Un secret resté sur le poste.** Une procédure qui lit un vault local (SOPS, `~/.config`, variable d'environnement du shell) échoue en routine : il n'y a pas de machine. Le credential doit vivre dans le coffre oto, résolu par un connecteur.
2. **Un fichier ou un dépôt local.** Même cause.
3. **L'heure.** Le cron est en UTC ; en France il faut retrancher une ou deux heures selon la saison.
4. **Un prompt qui recopie la procédure** au lieu de la charger.
5. **Un payload traité comme une instruction** — la routine l'ignore, et le run part sur le mauvais périmètre sans le dire.

## Ce qu'une routine ne fait pas

- **Pas d'idempotence.** Chaque déclenchement crée une session ; il n'existe pas de clé d'idempotence, et un retour de webhook produit un second run. La déduplication se fait dans la procédure (revérifier l'état avant d'écrire) ou en amont du déclenchement.
- **Elle appartient à un compte individuel.** Elle agit sous l'identité de son créateur, avec ses accès, et compte contre son plafond quotidien de runs. Ce n'est pas un compte de service.
- **Elle ne se provisionne pas depuis un tiers.** Seul `/fire` est une API publique : créer une routine et générer son jeton restent des gestes d'interface.

## Lire le résultat

Chaque run est une **session consultable** : le transcript complet, les appels d'outils, les erreurs. C'est ce qui distingue une routine d'un job planifié maison — la supervision est fournie.

Attention au statut : un run vert signifie que la session a démarré et s'est terminée sans erreur d'infrastructure. **Il ne dit pas que la tâche a réussi.** Les échecs de la tâche, les outils manquants et les requêtes réseau bloquées apparaissent dans le transcript, pas dans le voyant.

Le vrai réflexe : **faire écrire à la procédure une trace datée dans un tableau du datastore.** Un run devient alors lisible d'un coup d'œil, sans ouvrir un transcript — et l'historique s'accumule là où on sait déjà chercher.

## Déclencher une routine depuis oto

Le connecteur `routine` porte une routine par instance (`routine_id` + jeton de déclenchement, posés depuis les connecteurs du dashboard). `routine_fire` la déclenche et rend la session à ouvrir ; le résultat se lit dans cette session, pas dans la réponse.

C'est utile quand le déclencheur est un agent en conversation, ou un service qui passe déjà par oto. Un outil tiers qui sait faire un POST HTTP appellera `/fire` directement — inutile de mettre oto sur ce chemin.
