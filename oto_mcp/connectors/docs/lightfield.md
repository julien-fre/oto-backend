## prerequisite — lightfield api key

create an API key in Lightfield (Settings → API keys, admins only — [docs](https://docs.lightfield.app/using-the-api/api-keys/)), then paste it into oto.
- byo-only: no shared oto key — it is your CRM's data, each organization sets its own
- ⚠️ **scopes are chosen at key CREATION and cannot be added afterwards.** Tick at least `accounts:read`, `contacts:read`, `opportunities:read`; to write, add the matching `:create` and `:update`; to send email, `emails:create`. A key without CRM read is refused by the "test connection" button, which will tell you the scopes actually granted
- sending email additionally requires a Google or Microsoft mailbox **connected in Lightfield** by the key owner

## usage — the CRM whose fields belong to you

Lightfield's field model is specific to EACH workspace: the keys are the ones your team created, not universal names.
- before the first write → `lightfield_accounts(op="definitions")` (same for contacts, opportunities, notes, tasks): it is the list of valid keys
- "which companies in the CRM match…" → `lightfield_accounts(op="search", filters={...})`
- "the up-to-date state of this company" → `lightfield_accounts(op="get", record_id="…")`
- "create / update this company" → `lightfield_accounts(op="upsert", fields={...})` (add `record_id` to update)
- custom objects → `lightfield_objects(op="list")` then `op="definitions"` on the returned slug
- write an email from the connected mailbox → `lightfield_emails(op="send", sender="…", to=[...], dry_run=False)`

## note — three costly pitfalls

- ⚠️ **`op="search"` reads an index that may lag.** After a write, re-read with `op="get"`: search may return the state from BEFORE. This is stated in the vendor docs, and it shows most when chaining write-then-verify
- ⚠️ **`limit` caps at 25** (imposed by the API): beyond that, paginate with `offset`
- ⚠️ **sending can neither reply nor forward**: `op="send"` ALWAYS creates a new message, never a reply in an existing thread — if the context matters, quote it yourself in the body. And `op="send"` is **dry-run by default**: you need `dry_run=False` for anything to go out
