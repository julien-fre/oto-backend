---
title: L'agent intégré à l'application (copilot)
type: explanation
description: >-
  La boucle de l'agent qui travaille DANS l'application, côté serveur : quatre routes REST
  en flux (SSE), les outils de la personne exécutés en interne comme la cible d'`oto_call`,
  une porte d'approbation bâtie sur les DÉCLARATIONS des outils, des conversations en base
  liées à leur compte, une annulation lue dans le journal des révisions, et des appels au
  modèle réservés puis réglés au budget d'unités. Fermé par défaut (`OTO_COPILOT_SUBS`).
---

# L'agent intégré à l'application (copilot)

Le front ouvre un panneau où la personne parle à un agent qui manipule le produit pour
elle : ses agents, procédures, projets, docs, tableaux, connecteurs. **La boucle tourne
ici**, jamais dans le front (qui ne détient aucune clé de modèle). Le contrat de fil est
celui du front (`src/lib/copilot/protocol.ts`, `transport.ts`) ; le serveur de référence
de développement du front en était la spécification exécutable.

## Les routes (REST seule, `capabilities/copilot/routes.py`)

| route | réponse |
| --- | --- |
| `POST /api/copilot/turns` `{thread_id, text, input, mode, page_context, client_tools}` | flux SSE |
| `POST /api/copilot/threads/{id}/client-results` `{call_id, ok, result, error, client_tools}` | flux SSE |
| `POST /api/copilot/threads/{id}/approvals/{approval_id}` `{decision, edits, remember}` | flux SSE |
| `POST /api/copilot/actions/{revert_id}/revert` | JSON `{ok, undone, skipped}` |

- **Nées capacités** : un handler qui rend un `EventStream` est servi par l'adaptateur REST
  en `text/event-stream` (`api/base._file_stream`, CORS compris) ; `RestBinding.media` le
  publie dans `/openapi.json`. ⚠️ Le corps s'écoule APRÈS le handler : le flux pose lui-même
  identité, org et geste, et rend ses échecs DANS le flux (`error` puis `done`).
- **Qui** : membre de l'org consultée (`X-Oto-Org`), et `sub` dans `OTO_COPILOT_SUBS`
  (vide = personne → 403 `copilot_not_enabled`). Jamais en « voir en tant que » (403).
  ⚠️ Pas de 404 sur la route des tours : le front y lit « backend absent ».
- Une conversation est **liée à son `sub`** : un autre compte reçoit « introuvable », une
  autre org en ouvre une neuve.

## La boucle (`boucle.py`, `modele.py`)

Messages Anthropic en flux (`claude-opus-5-5`, effort `medium`, réflexion adaptative
`display: "updates"` sous la bêta `thinking-display-updates-2026-08-18` : les notes entre
deux appels d'outils deviennent les événements `progress`), cache sur le prompt système et
sur le dernier outil du socle. **L'historique ne se réécrit jamais** : les blocs de
réflexion repartent tels quels, un tour interrompu se solde en AJOUTANT des résultats
« interrompu ». `<next>a | b</next>` en fin de réponse devient `suggestions`, jamais du
texte. Un résultat d'outil est coupé à 80 000 caractères, et la coupe est dite au modèle.
30 étapes au plus par étape de la personne.

## Les outils (`outils.py`, `classement.py`)

- **La boîte de la personne** : ce que la poignée de main lui montre pour (sub, org)
  (`session_visibility.compute_hidden_tools`), moins ce que l'agent intégré n'appelle
  jamais (`oto_admin_*`, `oto_clear_*`, `oto_use_*`, `oto_shell`, `oto_account_access`,
  `oto_enable_tool`/`oto_disable_tool`, `run_start`/`run_finish`, `oto_run_thread`,
  `oto_call` imbriqué). Un socle est offert au modèle ; le reste se joint par `oto_call`.
- **Exécution en interne** par `tools/meta.executer_cible` : mêmes gardes, même journal,
  même rédaction, même règlement que la cible d'`oto_call` — sous `sub_override`, un
  contexte FastMCP lié à l'instance servie, et un **geste propre à l'appel**.
- **Les axes sont ceux du serveur** : `_org` (l'org consultée), `_project` (celui de la
  page), `_run_id` (un run par conversation) posés sur l'appel ET dans les arguments de la
  cible d'`oto_call` ; `_group` retiré ; ils ne sont pas offerts au modèle.

## La porte d'approbation

Bâtie sur les déclarations (`tools/lecture.py`) : `readOnlyHint` → lit ; `destructiveHint`
(`ENVOI`/`ACTE`) → demande toujours. Les outils du socle sans annotation ont une table
des `op` qui lisent (`classement.OPS_EN_LECTURE`) ; **tout ce qui n'est pas déclaré lecteur
demande**. Une écriture sur les objets de la personne (tableau, doc, projet), sans URL
extérieure, demande sauf « ne plus demander » pour cette sorte (outil + op) dans la
conversation, ou le mode `act` ; tout le reste (envoi, suppression, lancement, écriture qui
pilote d'autres agents, accès, appel sortant que l'agent vise lui-même, écriture portant
une URL extérieure, outil non déclaré) demande TOUJOURS et ne se retient jamais. Le titre
de la carte est écrit par le serveur et nomme chaque argument ; seuls les champs offerts
par la carte se modifient ; la réponse doit viser l'appel qui attend, de la sorte qui
attend.

## Annuler (`annulation.py`)

Les lignes se défont depuis le **journal des révisions** (`datastore_row_revisions`) : les
révisions du geste de l'appel, relues depuis un repère pris juste avant, et seulement les
cellules qui portent encore ce que l'agent a écrit (une cellule modifiée depuis est laissée
et comptée). Un tableau créé n'est supprimé que vide ; une modification de champs n'est
défaite que si le schéma stocké est encore celui que l'agent a laissé. Une fois, une heure.
Seuls les `data_*` en ont une.

## Payer le modèle (`cle.py`)

L'ordre d'un travail hébergé : la clé DÉPOSÉE par l'org (jamais mesurée) ; sinon une clé
PARTAGÉE accordée à l'org — refacturée (plateforme, tenant primaire), chaque appel est
**réservé avant** (`max_tokens`) et **réglé après** aux quatre comptes de jetons
(`access/credits_tours`, clé `copilot:<conversation>:<rang>`) ; sinon refus nommé.
⚠️ **Jamais d'appel gratuit sur une clé refacturée** : une réservation que le registre n'a
pas tenue (mode `report` sans les lignes du barème pour le modèle) REFUSE l'appel, à la
différence d'un agent hébergé ; seul le mode `off`, choix explicite de l'opérateur, laisse
passer. Une erreur HTTP du fournisseur libère la réservation ; un flux coupé la règle au
montant réservé.

## L'état (`db/copilot.py`, révision `20261025_2100_copilot`)

`copilot_threads` (messages, file, carte en attente, sortes retenues, actions de page, mode,
run, rang du prochain appel, verrou) et `copilot_undos`. **Une étape à la fois** par
conversation, par un verrou EN BASE à échéance (bleu, vert et préprod partagent la base).
Les runs des conversations inactives depuis 30 minutes se closent paresseusement, au tour
suivant de la personne ; une conversation reprise rouvre un run.
