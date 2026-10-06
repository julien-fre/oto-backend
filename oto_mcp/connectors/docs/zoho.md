## prerequisite — zoho crm self-client oauth (3 fields)

zoho crm uses a **self-client oauth2** with 3 secrets. in the [zoho api developer console](https://api-console.zoho.com), create a client of type **self client**, then generate a grant token and exchange it for a refresh token. you must provide oto with:
- **client_id** — the self client's id
- **client_secret** — its secret
- **refresh_token** — the refresh token obtained from the exchange (scopes `ZohoCRM.*`)
enter these 3 fields in oto on your account (`/account`), connector **zoho**. byo only.

## usage — what you can do

generic crud on your zoho crm modules (contacts, leads, deals, accounts…) from claude.
- "list my modules" → `zoho_modules`, "list the deals" → `zoho_record` (op `list`)
- "find the contact whose email = a@b.com" → `zoho_record` (op `search`, zoho criteria)
- "create a lead" → `zoho_record` (op `create`), "update this deal" → op `update`
- "add a note to this record" → `zoho_note` (op `create`)
