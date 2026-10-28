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
| `agent_tools` | `trigger_id` + `tools` | propriétaire, admin ou éditeur de l'agent | `runner.triggers` op=update, outils actuels + accordés |
| `team_join` | `group_id` | le chef de l'équipe (ou l'admin de l'org) | `group.member.add` |

## Les règles

- **La demande n'accorde rien.** Approuver rejoue la capacité qui accorde déjà ce geste,
  **sous l'identité de qui approuve** : sa règle d'autz, la garde d'org suspendue, son
  handler. Aucun droit n'est réécrit ici — qui ne pourrait pas accorder à la main ne
  peut pas approuver, et le refus est celui du geste (un connecteur coupé au-dessus de
  l'org reste coupé).
- **L'org de l'appel.** Une demande naît dans l'org active du demandeur, sur une cible
  qui vit dans cette org ; une cible d'une autre org se refuse comme une cible inconnue
  (404 `target_not_found`). Elle ne se lit ni ne se décide que depuis cette org.
- **On ne demande pas ce qu'on a** (409 `already_granted`), ni ce qu'on peut se donner
  (`agent_tools` : 409 `you_can_grant`). On ne décide pas sa propre demande.
- **Une seule en attente** par (demandeur, geste, cible) : redemander rend celle qui
  attend (`created: false`). Pour `agent_tools`, la cible est l'agent : une seconde
  demande sur le même agent rend la première, même avec d'autres outils.
- **Qui décide peut accorder moins** (ou plus) : `role` pour une ressource, un
  sous-ensemble des outils demandés pour un agent. `decision` garde ce qui a été
  réellement accordé.
- **Échéance : 14 jours.** Une demande échue n'est plus en attente (servie `expired`,
  indécidable) ; la redemander la passe `expired` en base et en crée une neuve.
- **Une décision, une seule** : chaque transition est un `UPDATE … WHERE status =
  'pending'` conditionnel. Deux approbations croisées accordent deux fois (le geste est
  idempotent) et ne closent qu'une fois.

## Pas encore

- Prévenir : aucune notification ne part à la demande ni à la décision, en dehors de
  celle du geste rejoué (un partage de ressource écrit déjà au destinataire). L'écran
  lit `box=review` pour son badge.
- Face agent (`oto_request`) et gestes de facturation : hors de ce lot.
