## prerequisite — clé api minari

crée une clé API dans Minari (**Settings → API & webhook**), puis colle-la dans oto.
- la clé porte les droits de **toute l'entreprise**, pas d'un utilisateur : elle voit les appels de tous les commerciaux de l'espace, et tout ce qu'elle écrit est écrit au nom de l'entreprise
- byo-only : pas de clé oto partagée. c'est votre journal d'appels
- **60 requêtes par minute, par entreprise** — pas par clé ni par personne : deux automatisations qui tournent sous la même clé se partagent ce budget. un `429` remonte avec le nombre de secondes avant réarmement

## usage — appels transcrits, listes à composer, analytics d'équipe

minari est un composeur d'appels sortants : l'équipe compose, minari enregistre, transcrit, résume et détecte les objections. six tools :

- « qu'est-ce qui s'est dit sur le prix cette semaine ? » → `minari_call(op="list", transcript_search="prix", start_date="…", end_date="…")`
- « quels appels ont donné un rendez-vous ? » → `minari_call(op="list", status=["meeting-booked"])` — ⚠️ `meeting-booked` est une valeur de **filtre uniquement** : une ligne d'appel ne la porte jamais comme `status`, elle porte le booléen `meeting_booked`
- « le résumé et les objections de cet appel » → `minari_call(op="get", call_id="…")` — rend la fiche **sans** le transcript (voir les bornes plus bas)
- « le verbatim complet » → `minari_call(op="transcript", call_id="…")`
- « y a-t-il un enregistrement ? » → `minari_call(op="recording", call_id="…")` — dit s'il existe et sa taille, **sans** rapatrier l'audio ; pour le faire écouter à quelqu'un, c'est `public_call_link` de la fiche d'appel, une page partageable sans clé — **null** si le partage externe est désactivé pour l'entreprise, donc à vérifier avant de promettre un lien
- « quel est notre taux de décroché ce mois-ci ? » → `minari_analytics(op="overview", start_date="…", end_date="…")`
- « qui décroche le plus dans l'équipe ? » → `minari_analytics(op="users", start_date="…", end_date="…")`
- « sur quoi bute-t-on le plus, et est-ce qu'on s'en sort ? » → `minari_analytics(op="objections")`
- « quelles listes sont à l'arrêt ou épuisées ? » → `minari_analytics(op="lists", period="week", call_limit=3)`
- « pousse cette liste de prospects dans le composeur » → `minari_user()` pour l'id du commercial, puis `minari_list(op="create", name="…", assigned_to=…, contacts=[…])`
- « ajoute ces 50 contacts à la liste en cours » → `minari_contact(op="add", list_id="…", contacts=[…])`
- « déclare un champ `industry` avant l'import » → `minari_custom_field(op="create", field_id="industry", label="Industrie")`

un contact d'import demande au moins un de `firstName`, `lastName`, `email` ; il accepte aussi `company`, `title`, `companyDomain`, `linkedinUrl`, `description`, `phoneNumber1`…`phoneNumber5`, une `note` (5000 caractères) et des `customFields` déclarés au préalable.

## note — ⚠️ les listes ne montrent que les imports CSV

c'est le piège n°1 de cette API, et il est silencieux : `minari_list` et `minari_contact` ne voient **que la source import CSV**. si vos contacts arrivent de HubSpot, Salesforce ou d'une autre synchro CRM, vos listes existent bel et bien dans Minari mais **n'apparaîtront jamais** par ces tools — un résultat vide veut dire « aucune liste CSV », pas « aucune liste ».

la vue **toutes sources** est `minari_analytics(op="lists")` : c'est elle qui répond à « où en sont mes listes ». les appels et les analytics couvrent, eux, toutes les sources.

## note — bornes de lecture

trois réponses peuvent exploser, et le connecteur les borne **en le disant** plutôt qu'en tronquant en silence :

- **la fiche d'appel embarque tout le transcript.** `minari_call(op="get")` le retire donc et rend `transcript_utterances` (le nombre de répliques) ; le texte s'obtient par `op="transcript"`, l'endpoint que Minari a justement séparé pour ça
- `op="transcript"` s'arrête à **200 répliques** par défaut (`max_utterances`) et rend le total réel plus un drapeau `truncated`
- **une liste rend ses 1500 contacts d'un bloc**, sans pagination : `minari_list(op="get")` s'arrête à **100** contacts par défaut (`max_contacts`) et rend `total_contacts` plus `truncated`

pagination : `minari_call(op="list")` et `minari_list(op="list")` rendent 50 lignes par page, `minari_analytics(op="lists")` en rend 10 — tailles fixées par Minari, non négociables. chaque réponse porte un `next_cursor`.

⚠️ **le curseur est une POSITION, pas une requête** — il se décode en `{"s": <date de début>, "c": <id d'appel>}` et ne porte aucun filtre. tourner la page en ne repassant QUE le curseur rend donc la suite du journal **entier**, non filtrée, sans la moindre erreur : il faut repasser les mêmes filtres à côté du curseur. la réponse le rappelle dans `note` à chaque fois qu'une page suivante existe.

## note — les fenêtres par défaut des analytics ne sont pas les mêmes

`op="overview"` et `op="users"` portent sur **aujourd'hui** quand on ne donne pas de dates ; `op="objections"` porte sur les **7 derniers jours**. demander « nos chiffres » sans préciser de période ne rend donc pas « tout », mais la journée en cours — et la réponse dit toujours dans `period` la fenêtre réellement retenue, à lire avant de conclure.

les taux sont des pourcentages (0–100) et valent `null` quand leur dénominateur est nul. les analyses sont à la journée près.

deux paramètres de `op="lists"` sont **obligatoires** parce qu'ils définissent le vocabulaire : `period` (la fenêtre de comptage des appels) et `call_limit` (le nombre de tentatives après lequel un contact jamais joint est considéré épuisé). deux valeurs différentes donnent deux réponses différentes — ce sont deux questions, pas une incohérence.

## note — écrit sur contrat, pas encore vérifié en live

ce connecteur a été écrit à partir de l'OpenAPI 3.1 publié par Minari (`api.minari.ai/docs/openapi.json`) et de son guide d'usage pour agents, **sans sonde contre un vrai compte** (2026-08-31). tout ce qui précède est donc une lecture du contrat, pas une mesure.

le premier vrai test est le bouton **« tester la connexion »** de cette fiche : il lit les membres de l'entreprise, ce qui prouve que la clé authentifie. il ne juge pas ce qu'elle rend — un annuaire vide n'est pas un motif de refus, et la sonde tourne avant l'enregistrement : ce qu'elle refuse ne serait jamais sauvegardé.

## note — ce que minari n'expose pas

l'API publique ne permet **ni de déclencher un appel**, ni de modifier un contact existant, ni de gérer les utilisateurs. ces gestes se font dans Minari. les webhooks (`call.completed`, `call.meeting_booked`, `contact.updated`) se configurent aussi depuis **Settings → API & webhook** : il n'existe aucun endpoint pour les créer ou les lister, donc aucun tool ici.

supprimer une liste (`minari_list(op="delete")`) ou un champ personnalisé (`minari_custom_field(op="delete")`) est **définitif** et retire la liste de la file d'appel du commercial. retirer des contacts d'une liste sans la détruire, c'est `minari_contact(op="remove")`.
