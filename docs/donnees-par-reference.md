---
title: Données par référence — ce qui ne passe plus par les arguments d'outil
type: explanation
description: >-
  Pourquoi et comment un geste de prospection ne porte plus ni URL extérieure ni donnée
  de personne dans ses arguments : le receveur des téléphones Apollo (URL générée par
  oto, jeton par commande, stockage 30 jours, lecture oto d'abord puis Apollo en repli,
  `webhook_url` retiré du schéma mais accepté) et les poussées de lignes par référence
  (`lemlist_push_rows`, `hubspot_push_rows` : le serveur lit les lignes, écrit en retour
  l'id et l'état, ne rend que des comptes et des codes). À lire avant d'ajouter un
  `<ns>_push_rows` ou un receveur.
---

# Données par référence

Un client MCP peut examiner chaque appel d'outil avant de l'envoyer — et en refuser un
qui transporte une donnée de personne (email, téléphone, nom) ou une adresse hors de
l'environnement de l'utilisateur. Un accord donné dans la conversation ne lève pas ce
refus. Deux gestes courants de prospection tombaient dedans : le reveal de téléphone
Apollo (une `webhook_url` fournie par l'appelant) et la création de leads/contacts (la
fiche de la personne en arguments, une fois par ligne).

**La règle : la donnée cesse réellement de passer par les arguments.** Ni encodage, ni
renommage de champ, ni valeur découpée — c'est le SERVEUR qui la déplace. L'agent
désigne (un tableau, des lignes, un `request_id`) ; oto lit, appelle, écrit en retour.

## 1. Le receveur des téléphones Apollo

Apollo ne rend jamais un mobile dans sa réponse : il vérifie les numéros et les POSTe
quelques minutes plus tard à une `webhook_url` qu'il EXIGE. Cette URL est désormais
générée par oto, à chaque reveal (`apollo_receiver.py`) :

    POST <OTO_MCP_PUBLIC_URL>/api/receivers/apollo/phones/{token}

- **Le jeton est l'autorisation.** 256 bits d'aléa, propre à UNE commande, stocké en
  empreinte SHA-256 seulement, masqué au journal comme tout paramètre `{token}`. Inconnu,
  abandonné ou expiré : 404, rien d'écrit. Un jeton qui n'a pas la forme émise (43
  caractères base64url) rend 404 avant que le corps soit lu.
- **La commande naît AVANT l'appel à Apollo** (table `apollo_phone_reveals`, révision
  `0027`) : Apollo peut livrer avant de nous rendre la main. Apollo ne trouve personne ou
  l'appel échoue : la commande est retirée. Sinon son `request_id` la rejoint.
- **Une livraison se garde une fois.** Une retentative répond 200 `duplicate: true` et
  n'écrase rien. Corps borné à 1 Mo, jugé en flux (413) ; illisible, 400. Seule une
  panne de notre côté rend 500 — le seul cas où la retentative d'Apollo est la bonne.
- **Lit qui a accès au connecteur, par la clé qui a payé.** La commande garde la portée
  de la clé Apollo résolue (`niveau:entité:compte`, `apollo_receiver.portee`). Le lecteur
  résout sa clé Apollo AVANT toute lecture ; il ne voit que les commandes payées par
  cette même clé. Sans accès au connecteur, rien n'est lu ; deux comptes sans org, chacun
  sur sa clé, ne se lisent pas.
- **L'id rendu par l'appel fait foi** : il remplace celui d'un POST arrivé avant, qu'Apollo
  ré-écho en nombre (abîmé par le float64).
- **L'index `(cle_portee, request_id)` n'est PAS unique**, délibérément : trouvé au banc, un
  index unique faisait répondre 500 à la seconde commande d'un même identifiant, et
  Apollo réessaie un 500 sans fin. L'unicité d'une livraison est tenue par le jeton.
- **Rétention : trente jours**, celle d'Apollo. La lecture filtre l'échéance ; la purge
  est un travail de maintenance (`oto-mcp maintenance apollo-phones`, dans `all`).

`apollo_reveal_phone_result` résout la clé Apollo, puis lit **ce qu'Apollo nous a livré
d'abord** — sans appel — et ne sonde Apollo (`webhook_result/{id}`) qu'en repli : POST pas encore
arrivé, refusé, ou commande antérieure à ce lot. La réponse garde sa forme (`done`,
`retry_after_seconds`, `result.webhook_result.people[].phone_numbers[]`,
`dnc_status_cd`). Avec `datastore` (+ `row_id`, ou `match_column` pour un lot), les
numéros vont directement dans le tableau — le premier mobile dans `phone_column`, son
type et son `dnc_status_cd` dans la couche `comment` — et seuls des comptes reviennent.

⚠️ **La forme du corps POSTé n'a pas été relevée en vrai.** Apollo documente que son
sondage rend « le même contenu que le POST » : le corps est stocké et servi tel quel.
Une seule forme de repli est reconnue, et nommée (`apollo_receiver.enveloppe`) : un
`people[]` de premier niveau, rangé sous `webhook_result`. Le premier reveal réel dira
laquelle vaut ; si c'est une troisième, le repli Apollo continue de servir.

**`webhook_url` est retiré du schéma, pas du contrat.** Une procédure écrite avant ce
lot le passe encore : il est accepté (`exclude_args` de FastMCP), IGNORÉ, et la réponse
porte `deprecation`. `exclude_args` est déprécié côté FastMCP ; le jour où il disparaît,
`tests/test_apollo_receveur.py` rougit sur l'appel qui le porte.

## 2. Les lignes poussées par référence

Les `waterfall_<x>_act(op="update_rows")` suivent ce contrat dans l'autre sens (le serveur lit des entrées, écrit une trouvaille : `docs/waterfall.md`). `lemlist_push_rows` et `hubspot_push_rows` prennent un tableau, des `row_ids` (≤ 50)
OU un `filter` (grammaire de `data_rows`), et une correspondance `{champ du connecteur:
colonne}`. La mécanique commune vit dans `datastore/par_reference.py` ; chaque outil ne
porte que son appel au connecteur (`tools/lemlist_lignes.py`, `tools/hubspot_lignes.py`).

- **Même store, mêmes règles que `data_write`** : org de l'appel, `_run_id` lu du
  contexte. Une ligne tenue par un autre run est écartée (`row_locked`) **avant**
  l'appel au connecteur — sinon le lead serait créé chez lui et l'écriture en retour
  refusée ici. Le droit d'écrire sur le tableau est vérifié avant tout envoi.
- **Écrit en retour** : `id_column` (l'id chez le connecteur) et `status_column`
  (`pushed|duplicate|failed` pour lemlist, `created|updated|exists|failed` pour
  HubSpot), le code d'un échec dans la couche `comment` de l'état.
- **Un existant n'est pas touché par défaut.** HubSpot : `on_existing="skip"` laisse
  intact un enregistrement qui existe déjà (état `exists`) ; `update` l'écrase avec les
  valeurs de la ligne, sur demande seulement. lemlist refuse lui-même un lead déjà pris
  (état `duplicate`). Dans les deux cas la réponse dit combien sont restés intacts
  (`existing_left_untouched`) et, pour HubSpot, comment les mettre à jour.
- **Une ligne se choisit une fois.** Par filtre, le lot ne retient que les lignes dont
  l'état est vide : rappeler avec le même filtre jusqu'à `remaining: 0`. Reprendre une
  ligne en échec = la nommer dans `row_ids`.
- **Le reçu ne porte que des comptes et des codes** — `errors: [{row_id, code}]`,
  l'identifiant de LIGNE, jamais une valeur lue. Un refus d'écriture en retour se
  réduit à son code : son texte pourrait citer la ligne.
- **Refusé avant tout envoi** : une colonne que le tableau ne connaît pas (faute de
  frappe dans la correspondance — une colonne libre vide sur tout le lot, elle, est
  connue), une liste HubSpot `DYNAMIC`, la propriété de rapprochement absente de la
  correspondance. `dry_run` lit et vérifie sans rien envoyer.
- **Budget d'horloge** de 30 s par lot : au-delà, reçu partiel (`stopped:
  "time_budget"`), le reste attend l'appel suivant — jamais un appel coupé sans reçu.
- **HubSpot rapproche sans deviner** : UNE recherche `IN` par lot sur la propriété
  d'unicité (`email`, `domain`) ; deux enregistrements pour une valeur font échouer la
  ligne (`hubspot_ambiguous_match`) plutôt que d'en choisir un. Deux lignes d'un lot à
  la même valeur désignent UN enregistrement : la seconde retrouve celui que la
  première vient de créer.

`lemlist_create_lead` et `hubspot_object` restent servis tels quels ; leur description
renvoie au `*_push_rows` pour le travail en lot.

## 3. Ce qui reste

Le relevé du 28/09/2026 (hors de ce dépôt) compte, en plus des cinq outils traités ici,
seize outils qui livrent à une URL fournie par l'appelant (webhooks lemlist/folk/grain/
granola/linear/tally/webflow/signwell, `lemlist_enrich*`, `fireflies_transcript`…) et
une soixantaine d'écritures qui prennent la personne en arguments (envois d'e-mails et
de messages, CRM, ATS). Le patron est le même : un receveur généré par oto pour les
premiers, un `<ns>_push_rows` ou un destinataire désigné par référence pour les seconds.
