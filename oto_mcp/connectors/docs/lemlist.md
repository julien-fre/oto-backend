## prerequisite — clé api lemlist

crée une clé API dans [lemlist](https://app.lemlist.com) (Settings → Integrations → API), puis colle-la dans oto (page compte / connecteurs).
- chacun voit SES campagnes : ta propre clé est requise
- l'enrichissement consomme des **crédits lemlist** (ceux de ton plan) : vérifie ton solde avant un gros volume

## usage — suivre tes campagnes de cold outreach

campagnes, leads, stats et activités.
- « liste mes campagnes lemlist et leur statut »
- « stats de la campagne X (envoyés, ouverts, répondus, bounces) »
- « quels leads ont répondu sur cette campagne ? »
- « montre les dernières activités (ouvertures, clics, réponses) »

créer/pauser une campagne et supprimer un lead restent hors périmètre : ça passe par l'UI lemlist.

## usage — enrichir un contact

lemlist enrichit sur quatre axes, indépendamment : trouver un email vérifié, vérifier un email existant, enrichir depuis LinkedIn, trouver un téléphone. **rien n'est demandé par défaut** — il faut nommer ce qu'on veut, chaque axe étant facturé.
- « trouve l'email de John Lempire chez lempire.com »
- « enrichis ce profil LinkedIn : téléphone + email »
- « vérifie que cet email est encore valide »
- « enrichis en masse ces 40 contacts, email seulement »

trois portes d'entrée :
- **un contact isolé**, sans campagne ni lead — l'identité est donnée à la main (URL LinkedIn, ou prénom/nom + domaine de l'entreprise ; lemlist ne résout que ce qu'il peut rapprocher)
- **un lead déjà dans une campagne** — l'identité vient du lead et le résultat est réécrit dessus
- **un lot** — un envoi, un id par personne

c'est **asynchrone** : la soumission rend un `enrichment_id` tout de suite, le travail continue chez lemlist, puis on relève le résultat. trois statuts : `done`, `in-progress`, `not-found`. compter **~30s à 1min30** en pratique (mesuré : 90s pour un email + téléphone). relever ne coûte aucun crédit.

le résultat porte un `found` qui ne liste que ce qui a vraiment une valeur : email, statut de vérification (`deliverable`/`undeliverable`), téléphone, profil LinkedIn. c'est ça qu'il faut lire — la charge brute contient toujours la clé de l'axe demandé, même vide, et son `notFound: false` a été vu sur une réponse sans numéro.

`enrich_lead` a une contrainte : il ne marche que sur un lead **en attente de review**. sur un lead déjà passé en review — c'est-à-dire tous ceux d'une campagne sans review-before-send — lemlist répond `lemrich is not available for lead reviewed`. sinon : enrichir la personne avec l'enrichissement autonome et reporter le résultat soi-même.

## note — enrichir à l'insertion vs enrichir tout court

les mêmes quatre axes existent **aussi** en options de `lemlist_create_lead`, appliquées au lead au moment où il entre dans la campagne. c'est le bon geste quand la campagne est la destination. quand tu veux juste la donnée — la poser dans un tableau, la croiser, la qualifier avant de décider — passe par l'enrichissement autonome : pas de campagne, pas de lead créé au passage.

## note — lemlist n'est pas la seule source d'enrichissement

oto porte plusieurs enrichisseurs (FullEnrich en waterfall multi-provider, Dropcontact, Kaspr, Hunter…). lemlist vaut surtout quand le contact est déjà dans ton outreach, ou pour rester sur un seul fournisseur et un seul compteur de crédits. pour un taux de réussite téléphone maximal sur un lot froid, compare avec FullEnrich.

## note — un `done` vide n'est pas forcément un « pas trouvé »

lemlist bascule parfois sur `done` **avant** d'avoir posé la charge utile : le relevé rend `done` avec un `data` vide, et le relevé suivant contient la donnée. c'est intermittent, pas systématique. le tool signale ces cas (`recheck_suggested`) : refaire **un** relevé avant de conclure que rien n'a été trouvé — ça ne coûte rien. après ce second relevé, un résultat toujours vide est un vrai « pas trouvé » (c'est le cas d'un profil LinkedIn que lemlist n'arrive pas à résoudre).

## note — le domaine d'entreprise est un indice, pas un filtre

lemlist rapproche sur ce qu'il peut et peut rendre un email sur un **autre** domaine que celui fourni : une recherche sur `zendesk.com` a rendu une adresse `@genesys.com`, la personne ayant changé d'employeur. c'est en général ce qu'on veut — mais si tu comptais sur le domaine comme garantie, vérifie le résultat.
