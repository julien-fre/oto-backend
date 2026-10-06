## prerequisite — an API V2 access (client id + secret)

In Sellsy: **Settings → Developer portal → API V2 → create an access**. Choose a **personal** access ("personal"): it is the one that issues a token directly from the id/secret pair, without going through a browser.

Then paste into oto:
- **Client ID** — the access identifier
- **Client Secret** — its secret (shown only once on the Sellsy side)

Tick the **rights (scopes)** matching what the agent will need to do: read-only to look things up, write to create third parties or documents. A missing right shows up as an `HTTP 403` refusal at call time, not at connection time.

byo only: a Sellsy account belongs to a company, there is no key shared by oto. The access inherits the permissions of the staff member it is attached to.

## usage — CRM and invoicing in the same conversation

Sellsy holds both ends: who the clients are, and what they are invoiced.
- "which companies were created this month?" → `sellsy_third_party` (op `search`)
- "who is Acme for us?" → `sellsy_search` (full text, all objects)
- "where does the pipeline stand?" → `sellsy_opportunity`, then op `move` to change step
- "list the unpaid invoices" → `sellsy_document` (kind `invoice`, op `search`, filter `status`)
- "make a quote for this client" → `sellsy_document` (kind `estimate`, op `create`) — it is born as a draft
- "which items are in the catalog?" → `sellsy_item`; "the VAT rates, the staff members" → `sellsy_ref`

Identifiers (pipeline step, tax, staff member, custom field) are read with `sellsy_ref` — never guess them.

## note — writing without breaking things

Two reflexes are worth knowing before letting the agent write.

**Dry run.** `op="create"` accepts `dry_run=true`: Sellsy validates the body and persists nothing. Useful before a serial creation, since required fields vary from one account to another (numbering, custom fields).

**Validating is irreversible.** A created document is a draft; `op="validate"` on an invoice or a credit note freezes its number and makes it accounting-relevant. Reserve it for a human decision. A quote, by contrast, simply changes state (`op="status"`).

Sellsy quotas are counted per second, minute, day and month, and **every request counts, even in error**: prefer `filters` + `fields` over a wide `all_pages`.
