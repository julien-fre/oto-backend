# Demandes d'accès

Une personne bloquée — « demande à un admin », « demande au propriétaire » — **demande**,
et qui peut le lui accorder l'accorde d'un geste. Avant, le produit disait seulement
« demande à… » : rien ne partait, personne n'était prévenu.

Capacité : `capabilities/demandes_acces.py`. Store : `db/demandes_acces.py`, table
`access_requests` (fragment `db/schema/grants.py::DEMANDES_D_ACCES`). Face REST seule.

## Les routes

| Verbe | Chemin | Qui |
|---|---|---|
| `POST` | `/api/me/access-requests` | tout membre de l'org active — demander |
| `GET` | `/api/me/access-requests?box=review` | ce que l'appelant peut décider, avec `pending_count` (le badge) |
| `GET` | `/api/me/access-requests?box=sent` | les demandes de l'appelant, toutes issues |
| `POST` | `/api/me/access-requests/{id}/approve` | qui peut accorder — `role` / `tools` ajustent ce qui est accordé |
| `POST` | `/api/me/access-requests/{id}/decline` | qui peut accorder — `note` facultative |
| `POST` | `/api/me/access-requests/{id}/cancel` | le demandeur |
| `POST` | `/api/me/access-requests/{id}/remind` | le demandeur, une fois par 24 h (la première un jour après la demande) |

## Les gestes (`kind`)

| `kind` | Cible | Qui décide | Ce qu'approuver rejoue |
|---|---|---|---|
| `resource_access` | `resource_type` ∈ project, datastore_namespace, procedure, doc + `resource_id`, `role` (viewer par défaut) | qui **gouverne** la ressource (`ownership.can_govern` : propriétaire, gérant, escalade `roles.py`) | `resources.govern` op=share, audience person |
| `connector_activation` | `connector` | un admin de l'org | `connectors.activation.set_org` enabled=true |
| `agent_access` | `trigger_id`, `role` viewer/editor (editor par défaut) | qui **gouverne** l'agent (propriétaire, admin de l'org) | `runner.triggers` op=share |
| `agent_tools` | `trigger_id` + `tools` | propriétaire, admin ou éditeur de l'agent | `runner.triggers` op=update, outils actuels + accordés |
| `team_join` | `group_id` | le chef de l'équipe (ou l'admin de l'org) | `group.member.add` |

## Les règles

- **La demande n'accorde rien.** Approuver rejoue la capacité qui accorde déjà ce geste,
  **sous l'identité de qui approuve** : sa règle d'autz, la garde d'org suspendue, son
  handler. Aucun droit n'est réécrit ici — qui ne pourrait pas accorder à la main ne
  peut pas approuver, et le refus est celui du geste (un connecteur coupé au-dessus de
  l'org reste coupé).
- **L'org de l'appel.** Une demande naît dans l'org active du demandeur, sur une cible
  qui vit dans cette org (une page vit dans l'org de son projet) ; une cible d'une autre org se refuse comme une cible inconnue
  (404 `target_not_found`). Elle ne se lit ni ne se décide que depuis cette org.
- **On ne demande pas ce qu'on a** (409 `already_granted`), ni ce qu'on peut se donner
  (`agent_tools` : 409 `you_can_grant`). On ne décide pas sa propre demande.
- **Une seule en attente** par (org, demandeur, geste, cible) : redemander rend celle qui
  attend (`created: false`). L'org est dans l'index unique, pas dans `cle_cible` : la clé
  d'un connecteur (`connector:<nom>`) ne la porte pas, et la même personne demande le même
  connecteur à chacune de ses orgs — deux demandes, une par org (la fusion de comptes
  dédoublonne de même, org comprise). Pour `agent_tools`, la cible est l'agent : une seconde
  demande sur le même agent rend la première, même avec d'autres outils.
- **Qui décide peut accorder moins** (ou plus) : `role` pour une ressource, un
  sous-ensemble des outils demandés pour un agent. `decision` garde ce qui a été
  réellement accordé.
- **Au plus 20 en attente** par personne et par org (`EN_ATTENTE_MAX`) : au-delà, une
  demande NEUVE est refusée (409 `too_many_pending_requests`) jusqu'à ce qu'une se décide,
  s'annule ou échoie ; redemander une demande qui attend la rend toujours. Plafond d'abus,
  compté sans verrou (deux créations simultanées peuvent le dépasser d'une).
- **Échéance : 14 jours.** Une demande échue n'est plus en attente (servie `expired`,
  indécidable) ; la redemander la passe `expired` en base et en crée une neuve.
- **Une décision, une seule** : chaque transition est un `UPDATE … WHERE status =
  'pending'` conditionnel. Deux approbations croisées accordent deux fois (le geste est
  idempotent) et ne closent qu'une fois. Approuver accorde PUIS clôt : un refus qui se
  glisse entre les deux clôt la demande `declined` alors que l'accès est accordé — rare,
  et le refus reste lisible dans `decision_note`.
- **Le demandeur parti n'obtient rien** : approuver revérifie qu'il est TOUJOURS membre
  (direct) de l'org de la demande, sinon 409 `requester_left` et rien n'est accordé — un
  partage de ressource sait viser une personne hors de l'org, il l'aurait posé. La demande
  reste en attente, déclinable, jusqu'à son échéance.
- **Les refus du geste rejoué traversent** `approve` tels quels (`forbidden`,
  `trigger_edit_forbidden`, `platform_disabled`…), non déclarés ici : l'écran affiche
  `error` et son `detail`.
- **Pas d'escalade « admin en secours »** : qui décide est qui peut déjà accorder. Un
  admin de l'org ne voit donc pas la demande d'accès au projet PERSONNEL d'un membre —
  il ne pourrait pas le partager à la main non plus.
- **Le nom de la cible** (`target_label`) d'une ressource ne se sert qu'à qui peut déjà
  la lire ou décider : demander par id ne lit pas, un à un, les noms des objets privés
  des collègues. Le demandeur le reçoit une fois l'accès accordé. Même règle pour un agent
  (`agent_access`). Le nom d'une **équipe** se sert à tous : aucune équipe n'est cachée aux
  membres de son org (`group.list` les nomme toutes).
- **L'existence se lit, par id et dans l'org seulement** : demander l'accès à un projet ou
  à un agent PRIVÉ qu'on ne voit pas est l'objet même de `resource_access` / `agent_access`,
  donc « il existe dans cette org » se lit (201 ou 409 contre 404). Une cible d'une autre
  org est inconnue (404). `agent_tools` rend 404 pour un agent qu'on ne voit pas : on ne
  demande des outils que pour un agent qu'on lit déjà.
- **Connecteur fermé au-dessus de l'org** (plateforme, hôte) : refusé à la demande
  (409 `cut_above_org`) — personne dans l'org ne pourrait l'accorder.
- **Coût de `box=review`** : chaque demande en attente de l'org est jugée (`peut_decider`,
  quelques lectures) à chaque lecture. Un badge se relit sans hâte. Bornée, pas paginée :
  les 200 plus RÉCENTES (`REVUE_MAX`), la plus récente d'abord ; au-delà, `truncated: true`
  et `pending_count` est un minimum — ce sont les plus anciennes, proches de l'échéance,
  qui ne se servent pas.

## Pas encore

- `remind` ne fait qu'horodater : rien n'est envoyé tant qu'il n'y a pas de
  notification. L'écran ne montre pas de bouton « Relancer » d'ici là.
- Prévenir : aucune notification ne part à la demande ni à la décision, en dehors de
  celle du geste rejoué (un partage de ressource écrit déjà au destinataire). L'écran
  lit `box=review` pour son badge.
- Face agent (`oto_request`) et gestes de facturation : hors de ce lot.
