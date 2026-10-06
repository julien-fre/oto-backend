## prerequisite — zoho analytics oauth self-client (5 fields)

zoho analytics uses an **oauth2 self-client**. In the [zoho api developer console](https://api-console.zoho.com), create a **self client**, generate a grant token with the scopes `ZohoAnalytics.data.read` + `ZohoAnalytics.metadata.read`, then exchange it for a refresh token. you must provide oto with:
- **client_id** and **client_secret** — from the self client
- **refresh_token** — from the exchange
- **org_id** — the id of your analytics organization (`ZANALYTICS-ORGID` header)
- **data_center** — your zoho region (`com`, `eu`, `in`, `au`, `jp`, `ca`, `sa`), visible in the url (e.g. analytics.zoho.eu → `eu`)
fill in these 5 fields in oto on your account (`/account`), connector **zohoanalytics**. byo (personal or org).

## usage — what you can do

query your zoho analytics data from claude.
- "list my workspaces" → `zohoanalytics_workspaces`
- "which views in this workspace" → `zohoanalytics_views`
- "export the data of this table" → `zohoanalytics_export` (json by default)
- "sales by region" → `zohoanalytics_query` (sql select query on the workspace's tables)
