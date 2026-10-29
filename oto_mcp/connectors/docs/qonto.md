## prerequisite — qonto api key

an **owner or admin** in Qonto opens Settings → Integrations and partnerships → API key. The page shows the **login** (like `acme-1234` — not an email) and lets you generate a **secret key**. Paste both into oto joined by a colon: `login:secret-key`.
- byo-only: no shared oto key — this is your bank account, each organization sets its own
- read-only: an API key reaches no payment route, and the connector never writes into Qonto
- the "test connection" button reads the organization and its accounts: no side effect

## usage — balances, transactions, invoices

- "what's on the bank account" → `qonto()` (op `accounts`: every account with `balance` and `authorized_balance`)
- "October's money in, with VAT" → `qonto(op="transactions", side="credit", date_from="2026-10-01", date_to="2026-10-31T23:59:59Z", includes=["vat_details"], all_pages=true)`
- "card spend without a receipt" → `qonto(op="transactions", operation_type=["card"], with_attachments=false)`
- "the receipt of this transaction" → `qonto(op="attachments", id="<transaction id>")` — the `url` lasts 30 minutes
- "supplier invoices to review" → `qonto(op="supplier_invoices", status=["to_review"])`
- "unpaid client invoices" → `qonto(op="client_invoices", status=["unpaid"])`
- "this year's statements" → `qonto(op="statements", date_from="01-2026", date_to="12-2026")`

## note — accounts, dates, what is not served

- transactions are read **per bank account**: without `bank_account_id` or `iban`, the main account is read
- `date_from`/`date_to` apply to `settled_at` for transactions — ⚠️ a pending transaction has no `settled_at` and is left out; use `date_field="emitted_at"` to include it
- amounts are in euros (`amount`) on transactions and decimal strings (`total_amount.value`) on invoices
- **not served**: cards, cash-flow categories, and any payment or write (transfers, beneficiaries, invoices) — Qonto opens them only to an OAuth connection, never to an API key
