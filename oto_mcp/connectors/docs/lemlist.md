## prerequisite — clé api lemlist

crée une clé API dans [lemlist](https://app.lemlist.com) (Settings → Integrations → API), puis colle-la dans oto (page compte / connecteurs).
- chacun voit SES campagnes : ta propre clé est requise

## usage — conduire tes campagnes de cold outreach

lecture : campagnes, leads, stats et activités.
- « liste mes campagnes lemlist et leur statut »
- « stats de la campagne X (leads touchés, ouverts, répondus, bounces) »
- « quels leads ont répondu sur cette campagne ? »
- « compare mes 5 campagnes du trimestre » (rapport multi-campagnes en un appel)

gestion : créer et régler une campagne, écrire sa séquence, tenir ses fenêtres d'envoi.
- « crée une campagne "Q4 outbound" en Europe/Paris »
- « ajoute une étape email à J+3 sur la séquence, sujet … »
- « quelles étapes a cette campagne ? » puis « supprime la 4ᵉ » (campagne en pause d'abord)
- « mets la fenêtre d'envoi sur 8h-12h du lundi au jeudi »
- « duplique cette campagne pour l'équipe NL »
- « qu'est-ce qui bloque le lancement de cette campagne ? » (la validation de l'UI : sender manquant, DNS cassé, limite journalière)

## note — ce qui envoie, et ce qui ne s'ouvre pas ici

deux gestes seulement mettent des messages sur le fil, et les deux sont **masqués par défaut** (activables à la demande, `oto_enable_tool`) :
- `lemlist_campaign_start` — démarrer la campagne fait dérouler la séquence pour tous ses leads lancés
- `lemlist_launch_lead` — sortir un lead de la revue manuelle

tout le reste travaille sur un brouillon : une campagne créée ou dupliquée naît en **draft**, une séquence s'écrit sans rien envoyer. pause ≠ rappel : mettre en pause arrête la progression, pas ce qui est déjà programmé.

**`autoReview` n'est pas réglable depuis oto** (ni à la création ni à la mise à jour). ce réglage lance tout lead dès son ajout : il ferait de l'ajout d'un lead un envoi, sans passer par aucun des deux gestes ci-dessus. il se règle dans l'UI lemlist, sur la campagne.

supprimer une étape est refusé par lemlist tant que la campagne tourne — mets-la en pause d'abord. les A/B tests demandent un plan Email Pro.
