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

c'est **asynchrone** : la soumission rend un `enrichment_id` tout de suite, le travail continue chez lemlist, puis on relève le résultat (~10-20s, puis toutes les ~15-30s). trois statuts : `done`, `in-progress`, `not-found`.

## gotcha — enrichir à l'insertion vs enrichir tout court

les mêmes quatre axes existent **aussi** en options de `lemlist_create_lead`, appliquées au lead au moment où il entre dans la campagne. c'est le bon geste quand la campagne est la destination. quand tu veux juste la donnée — la poser dans un tableau, la croiser, la qualifier avant de décider — passe par l'enrichissement autonome : pas de campagne, pas de lead créé au passage.

## gotcha — lemlist n'est pas la seule source d'enrichissement

oto porte plusieurs enrichisseurs (FullEnrich en waterfall multi-provider, Dropcontact, Kaspr, Hunter…). lemlist vaut surtout quand le contact est déjà dans ton outreach, ou pour rester sur un seul fournisseur et un seul compteur de crédits. pour un taux de réussite téléphone maximal sur un lot froid, compare avec FullEnrich.
