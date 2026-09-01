## prerequisite — obtenir une clé apollo

crée une clé api dans les réglages développeur/api de ton compte [apollo](https://app.apollo.io).
- colle-la dans tes connecteurs oto sur `/account`
- la clé hérite des crédits de ton plan apollo
- **seule la recherche/enrichissement d'entreprises et de personnes** (ci-dessous) admet
  une clé plateforme free-tier (quota quotidien) si tu n'en poses pas la tienne — elle
  interroge la base PARTAGÉE Apollo, la même pour tout le monde.
- quota épuisé : `apollo_match_person` refuse en NOMMANT le compteur (`used/limit`) et
  dit qu'il repart à minuit — la réponse d'un appel réussi porte aussi `platform_quota`
  (`used`/`limit`/`remaining`) pour t'arrêter avant le refus au milieu d'un lot. Pose ta
  propre clé pour lever la limite tout de suite, ou pour CE lead : `hunter_email_finder`
  (email) et `kaspr_enrich_linkedin` / `fullenrich_enrich_linkedin` (téléphone,
  historique LinkedIn) — source différente, pas de crédit brûlé sur un appel qui
  échouerait de toute façon.
- **contacts, séquences, emails et conversations sont BYO-only** — pas de repli plateforme
  sur ces outils-là, il te faut ta propre clé. Pas seulement pour écrire : même les lister
  ou les lire rend TES données (ton carnet de contacts, tes boîtes connectées, le contenu
  de tes emails envoyés, tes transcripts d'appels) — une clé plateforme mutualisée
  exposerait ça à n'importe quel autre utilisateur d'oto.
- **les outils de contact demandent en plus une clé « Master »** (Apollo → Settings →
  Integrations → API) : une clé standard authentifie mais rend 403 sur ces trois-là.

## usage — prospection b2b (entreprises + contacts)

recherche et enrichis entreprises et personnes, et repère les signaux de recrutement.
- `apollo_search_organizations` — entreprises par nom, domaine, pays
- `apollo_search_people` — personnes par domaines, départements, intitulés, séniorités
- `apollo_match_person` — enrichit une personne (url linkedin ou email = meilleurs identifiants)
- `apollo_job_postings` — offres d'emploi actives d'une entreprise (signal d'embauche)

## usage — contacts (les personnes DANS ton espace de travail)

- `apollo_contact` (`op=fields|search|get|update`) — lis et modifie un contact
  ENREGISTRÉ chez toi : titre, email, téléphones, stage, listes, champs personnalisés
- `op=search` retrouve un contact et son `contact_id` (tes contacts, pas la base
  partagée). ⚠️ L'autre source, souvent déjà payée : `apollo_match_person` porte le
  contact id IMBRIQUÉ à `person.contact.id`, dès que la personne est un contact chez
  toi. Un id d'`apollo_search_people` est un id de PERSONNE et sera refusé ici.
  Le même `contact_id` sert ensuite à `apollo_sequence_contacts(op=add)`
- ⚠️ un **contact ≠ une personne** : `apollo_search_people` interroge la base
  partagée Apollo, `apollo_contact` ne voit que ce que ton équipe a déjà
  enregistré. Une personne trouvée mais jamais enregistrée n'a pas d'id de contact
- `op=get` **ne coûte aucun crédit** — c'est la façon de relire un contact ;
  `apollo_match_person` en coûte un et rend la fiche partagée, pas tes valeurs
- `op=fields` d'abord pour une écriture de champ personnalisé : la charge utile
  est keyée par **id** de champ, jamais par nom. Pour une liste de choix, la
  valeur à écrire est l'`id` de l'option, pas son libellé
- `op=create_field` déclare un champ personnalisé sans passer par l'interface
  Apollo (utile quand le compte appartient au client). ⚠️ Pour un texte long —
  une accroche, un paragraphe — c'est `field_type="textarea"` : `string` est
  plafonné à 120 caractères et Apollo tronque sans rien dire. Un champ portant
  déjà ce nom fait REFUSER la création plutôt que d'en créer un homonyme
- ⚠️ ces trois appels demandent une clé Apollo **Master** (Settings →
  Integrations → API) ; une clé standard authentifie mais rend 403
- ⚠️ `label_names` REMPLACE l'appartenance aux listes au lieu de s'y ajouter
- `dry_run` disponible sur `op=update`

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
