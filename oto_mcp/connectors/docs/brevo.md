## prerequisite — your brevo api key (v3)

brevo authenticates with a **v3 api key**. in [your brevo account](https://app.brevo.com), go to **settings → smtp & api → api keys**, generate a key (it covers the whole account, no scope).
- copy the key (it starts with `xkeysib-`)
- paste it into oto on your account (`/account`), **brevo** connector
- byo only: your key or your org's shared one, no platform key
- not to be confused with **brevo (automation)**, a separate connector for automation scenarios (browser-session connection)

## usage — emailing & crm from claude

manage your brevo contact base, sends and crm.
- "add jean to the newsletter list" → `brevo_contact(op="upsert")` / `brevo_list(op="contacts")`
- "send this email to marie" → `brevo_send_email` (single transactional)
- "prepare a campaign for the clients list" → `brevo_campaign(op="create")` (draft; the mass send is triggered in the ui)
- "how many opens on my last campaign" → `brevo_campaign(op="list")` (statistics)
- "create a 10k€ deal" → `brevo_crm_create` (entity `deals`)
