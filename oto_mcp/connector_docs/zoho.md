## prerequisite — self-client oauth zoho crm (3 champs)

zoho crm utilise un **self-client oauth2** à 3 secrets. dans la [console développeur zoho api](https://api-console.zoho.com), crée un client de type **self client**, puis génère un grant token et échange-le contre un refresh token. tu dois fournir à oto :
- **client_id** — l'id du self client
- **client_secret** — son secret
- **refresh_token** — le refresh token issu de l'échange (scopes `ZohoCRM.*`)
renseigne ces 3 champs dans oto sur ton compte (`/account`), connecteur **zoho**. byo uniquement.

## usage — ce que tu peux faire

crud générique sur tes modules zoho crm (contacts, leads, deals, accounts…) depuis claude.
- « liste mes modules » → `zoho_modules`, « liste les deals » → `zoho_record` (op `list`)
- « trouve le contact dont l'email = a@b.com » → `zoho_record` (op `search`, criteria zoho)
- « crée un lead » → `zoho_record` (op `create`), « mets à jour ce deal » → op `update`
- « ajoute une note sur ce record » → `zoho_note` (op `create`)
