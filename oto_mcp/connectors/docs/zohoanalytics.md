## prerequisite — self-client oauth zoho analytics (5 champs)

zoho analytics utilise un **self-client oauth2**. dans la [console développeur zoho api](https://api-console.zoho.com), crée un **self client**, génère un grant token avec les scopes `ZohoAnalytics.data.read` + `ZohoAnalytics.metadata.read`, puis échange-le contre un refresh token. tu dois fournir à oto :
- **client_id** et **client_secret** — du self client
- **refresh_token** — issu de l'échange
- **org_id** — l'id de ton organisation analytics (en-tête `ZANALYTICS-ORGID`)
- **data_center** — ta région zoho (`com`, `eu`, `in`, `au`, `jp`, `ca`, `sa`), visible dans l'url (ex. analytics.zoho.eu → `eu`)
renseigne ces 5 champs dans oto sur ton compte (`/account`), connecteur **zohoanalytics**. byo (perso ou org).

## usage — ce que tu peux faire

interroge tes données zoho analytics depuis claude.
- « liste mes workspaces » → `zohoanalytics_workspaces`
- « quelles vues dans ce workspace » → `zohoanalytics_views`
- « exporte les données de cette table » → `zohoanalytics_export` (json par défaut)
- « ventes par région » → `zohoanalytics_query` (requête sql select sur les tables du workspace)
