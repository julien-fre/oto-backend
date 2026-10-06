## prerequisite — zoho desk oauth self-client (4 fields)

Zoho Desk uses an **oauth2 self-client** with 4 secrets. In the [Zoho API developer console](https://api-console.zoho.com), create a **self client**, generate a grant token with the `Desk.*` scopes, then exchange it for a refresh token. You need to provide oto with:
- **client_id** and **client_secret** — from the self client
- **refresh_token** — from the exchange
- **org_id** — the id of your desk organization (`orgId` header) — **optional**: a single-portal token resolves the portal on its own, only fill it in if a call asks for it

**scopes per surface** — a token can authenticate with PARTIAL scopes (articles respond while tickets return `SCOPE_MISMATCH`). Request the ones you need:
- tickets → `Desk.tickets.READ` (+ `.WRITE` to create/edit) · search → `Desk.search.READ`
- contacts → `Desk.contacts.READ` (+ `.WRITE`) · departments → `Desk.basic.READ` · articles (KB) → `Desk.articles.READ`

Fill in these fields in oto on your account (`/account`), connector **zohodesk**. byo only.

## usage — what you can do

Manage zoho desk support (tickets, threads, contacts) from claude.
- "list open tickets" → `zohodesk_tickets` (status `Open`)
- "open ticket #123 with its contact" → `zohodesk_ticket` (include `contacts`)
- "create a ticket" → `zohodesk_create_ticket` (subject + departmentId + contactId)
- "the replies on this ticket" → `zohodesk_ticket_threads`
