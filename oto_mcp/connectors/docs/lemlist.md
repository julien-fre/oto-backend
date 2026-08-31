## prerequisite — clé api lemlist

crée une clé API dans [lemlist](https://app.lemlist.com) (Settings → Integrations → API), puis colle-la dans oto (page compte / connecteurs).
- chacun voit SES données : ta propre clé est requise

## usage — l'API lemlist en entier

le connecteur reflète les **141 routes documentées**, sans exception. par famille :

**campagnes** — `lemlist_list_campaigns`, `lemlist_campaign`, `lemlist_sequence`, `lemlist_schedule`, `lemlist_get_campaign_stats`
- « crée une campagne "Q4 outbound", ajoute une étape email à J+3 »
- « qu'est-ce qui bloque le lancement de cette campagne ? » (sender manquant, DNS, limite journalière)
- « mets la fenêtre d'envoi sur 8h-12h du lundi au jeudi », « duplique-la pour l'équipe NL »
- « stats de la campagne X », « compare mes 5 campagnes du trimestre »

**leads** — `lemlist_create_lead`, `lemlist_lead`, `lemlist_enrich*`
- « ajoute ce lead, trouve son email et vérifie-le »
- « mets ce lead en pause sur toutes les campagnes », « marque-le intéressé »
- « importe les leads du filtre HubSpot X dans cette campagne »

**CRM lemlist** — `lemlist_contact`, `lemlist_company`, `lemlist_team(op="fields")`
- ⚠️ un **contact** n'est pas un **lead** : le lead est l'exemplaire d'une personne DANS une campagne, le contact est la personne elle-même. c'est la confusion la plus coûteuse ici.

**inbox** — `lemlist_inbox` (conversations, brouillons, libellés) et `lemlist_inbox_send`
**désinscriptions** — `lemlist_unsubscribe` : ⚠️ **trois listes distinctes** (emails/domaines v1, variables v2, drapeau do-not-contact d'un contact) ; écrire dans l'une n'écrit pas dans les autres
**signaux** — `lemlist_watchlist` : boîtes qui recrutent, levées, changements de poste…
**le reste** — `lemlist_task`, `lemlist_database` (base partagée + personas), `lemlist_team`, `lemlist_mailbox` (connexion SMTP/IMAP + lemwarm), `lemlist_deliverability`, `lemlist_webhook`, `lemlist_get_activities`

## note — ce qui envoie, et ce qui est masqué par défaut

quatre tools envoient ou **arment** l'envoi. tous les quatre sont **masqués par défaut** — ils restent appelables, il faut juste les activer (`oto_enable_tool <nom>`) :

- `lemlist_campaign_start` — démarrer la campagne déroule la séquence pour tous ses leads lancés
- `lemlist_launch_lead` — sortir un lead de la revue manuelle
- `lemlist_inbox_send` — email / LinkedIn / WhatsApp **directs** : ni campagne, ni séquence, ni revue devant eux, le message part
- `lemlist_campaign_auto_review` — n'envoie rien lui-même, mais fait partir tout lead **ajouté** ensuite : avec lui, `lemlist_create_lead` devient un envoi

tout le reste travaille sur un brouillon ou sur de la donnée. une campagne créée ou dupliquée naît en **draft**. pause ≠ rappel : mettre en pause arrête la progression, pas ce qui est déjà programmé.

deux surfaces envoient **indirectement**, et restent visibles en le disant : une watch list réglée sur `push_to_campaign` alimente une campagne toute seule, et `lemlist_mailbox(op="lemwarm_start")` envoie — mais dans le réseau de chauffe, jamais vers un prospect.

## note — pièges de l'API lemlist

- **supprimer un lead ≠ le désinscrire**, et lemlist sert les deux par la même route, le défaut étant le doux. ici les deux ops sont nommées à part (`op="delete"` vs `op="unsubscribe"`).
- **`lemlist_lead(op="pause")` sans `campaign_id` met le lead en pause sur TOUTES les campagnes**, pas sur une.
- **`lemlist_contact(op="list_manage")` AJOUTE par défaut** ; `action="remove"` retire.
- supprimer une étape est refusé tant que la campagne tourne — mets-la en pause d'abord. les A/B tests demandent un plan Email Pro.
- les enrichissements dépensent des crédits (`lemlist_team(op="credits")` les compte).
