## prerequisite — self-client oauth zoho desk (4 champs)

zoho desk utilise un **self-client oauth2** à 4 secrets. dans la [console développeur zoho api](https://api-console.zoho.com), crée un **self client**, génère un grant token avec les scopes `Desk.*`, puis échange-le contre un refresh token. tu dois fournir à oto :
- **client_id** et **client_secret** — du self client
- **refresh_token** — issu de l'échange
- **org_id** — l'id de ton organisation desk (en-tête `orgId`) — **facultatif** : un token mono-portail résout le portail tout seul, ne le renseigne que si un appel le réclame

**scopes par surface** — un token peut authentifier avec des scopes PARTIELS (les articles répondent pendant que les tickets rendent `SCOPE_MISMATCH`). demande ceux dont tu as besoin :
- tickets → `Desk.tickets.READ` (+ `.WRITE` pour créer/modifier) · recherche → `Desk.search.READ`
- contacts → `Desk.contacts.READ` (+ `.WRITE`) · départements → `Desk.basic.READ` · articles (KB) → `Desk.articles.READ`

renseigne ces champs dans oto sur ton compte (`/account`), connecteur **zohodesk**. byo uniquement.

## usage — ce que tu peux faire

gère le support zoho desk (tickets, threads, contacts) depuis claude.
- « liste les tickets ouverts » → `zohodesk_tickets` (status `Open`)
- « ouvre le ticket #123 avec son contact » → `zohodesk_ticket` (include `contacts`)
- « crée un ticket » → `zohodesk_create_ticket` (subject + departmentId + contactId)
- « les réponses de ce ticket » → `zohodesk_ticket_threads`
