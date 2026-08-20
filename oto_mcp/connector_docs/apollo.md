## prerequisite — obtenir une clé apollo

crée une clé api dans les réglages développeur/api de ton compte [apollo](https://app.apollo.io).
- colle-la dans tes connecteurs oto sur `/account`
- la clé hérite des crédits de ton plan apollo
- **seule la recherche/enrichissement d'entreprises et de personnes** (ci-dessous) admet
  une clé plateforme free-tier (quota quotidien) si tu n'en poses pas la tienne — elle
  interroge la base PARTAGÉE Apollo, la même pour tout le monde.
- **séquences, emails et conversations sont BYO-only** — pas de repli plateforme sur ces
  outils-là, il te faut ta propre clé. Pas seulement pour écrire : même les lister ou les
  lire rend TES données (tes boîtes connectées, le contenu de tes emails envoyés, tes
  transcripts d'appels) — une clé plateforme mutualisée exposerait ça à n'importe quel
  autre utilisateur d'oto.

## usage — prospection b2b (entreprises + contacts)

recherche et enrichis entreprises et personnes, et repère les signaux de recrutement.
- `apollo_search_organizations` — entreprises par nom, domaine, pays
- `apollo_search_people` — personnes par domaines, départements, intitulés, séniorités
- `apollo_match_person` — enrichit une personne (url linkedin ou email = meilleurs identifiants)
- `apollo_job_postings` — offres d'emploi actives d'une entreprise (signal d'embauche)

## usage — séquences (campagnes email automatisées)

- `apollo_email_accounts` / `apollo_email_schedules` — prérequis en lecture (TES boîtes
  connectées / plannings d'envoi), à appeler avant de créer une séquence ou d'y enrôler
  des contacts
- `apollo_sequence` (`op=search|create|update|activate|deactivate|archive`) — gérer une
  séquence
- `apollo_sequence_contacts` (`op=add|update_status|activity`) — enrôler/retirer des
  contacts, consulter leur activité. `add` démarre une campagne automatisée vers des
  personnes réelles — `dry_run` disponible

## usage — emails ponctuels (hors séquence)

- `apollo_email` (`op=draft|send|status|search|content|stats`) — `draft` prépare,
  `send` envoie (toujours deux appels distincts). `search`/`content` rendent TES emails
  envoyés (corps inclus), pas une base partagée

## usage — conversations (appels/visios enregistrés)

- `apollo_conversation` (`op=search|get|export|export_status`) — TES transcripts et
  enregistrements. Le coût crédit d'un `get` dépend de la présence d'insights IA,
  imprévisible avant l'appel
