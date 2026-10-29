## prerequisite — zoho sign oauth (connect with zoho, or a self-client)

zoho sign uses the same zoho oauth as the crm and desk. the simplest: click **authorize oto with zoho** on the connector card and pick your zoho region (data center) — nothing to copy. with a self-client from the [zoho api developer console](https://api-console.zoho.com) (use the console of your region, e.g. api-console.zoho.eu), provide oto with:
- **client_id** and **client_secret** — from the self client
- **refresh_token** — from the grant token exchange
- **data_center** — com, eu, in, au, jp or ca (the domain of your zoho sign url: sign.zoho.eu → eu)

**scopes per access level** — `read`: `ZohoSign.documents.READ`, `ZohoSign.templates.READ` · `full` (default): the same plus `ZohoSign.documents.CREATE` (send from a template) and `ZohoSign.documents.UPDATE` (remind, recall). nothing deletes.

## usage — what you can do

follow and send zoho sign signature requests from claude.
- "which requests are still waiting for signature?" → `zohosign` (op `list`, status `inprogress`)
- "who has signed the nda sent to a@b.com?" → `zohosign` (op `list`, search `recipient_email`), then op `get`
- "get the signed pdf" → `zohosign` (op `download_pdf`), the completion certificate → op `download_certificate`
- "send the partnership template to ann" → `zohosign` (op `get_template`) for its recipients and fields, then `zohosign_act` (op `send_from_template`, try `dry_run` first)
- "remind the signers" → `zohosign_act` (op `remind`), "recall this request" → `zohosign_act` (op `cancel`)
