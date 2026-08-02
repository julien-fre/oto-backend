## prerequisite — ton token hubspot (private app)

hubspot s'authentifie via un **token de private app**. dans les [réglages de ton compte hubspot](https://app.hubspot.com), va dans **integrations → private apps**, crée une private app et donne-lui les scopes crm voulus (contacts, companies, deals, tickets).
- copie le **access token** généré
- colle-le dans oto sur ton compte (`/account`), connecteur **hubspot**
- byo uniquement : ta clé ou celle partagée de ton org, pas de clé plateforme

## usage — ce que tu peux faire

interroge et édite ton crm hubspot (contacts, companies, deals, tickets) depuis claude.
- « cherche les contacts de chez acme » → `hubspot_search` (object_type `contacts`)
- « crée un deal à 10k€ » → `hubspot_create` (object_type `deals`)
- « les deals associés à ce contact » → `hubspot_associations`
- « ajoute une note sur ce contact » → `hubspot_create_note`
