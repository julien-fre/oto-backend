## prerequisite — your gocardless api key

read-only — each user sets their own key, your direct debits are visible only to you.
- from the [gocardless dashboard](https://gocardless.com), open developers then create access token
- choose a **read** (read-only) token — oto neither cancels nor creates direct debits
- paste it into your oto connector keys under `gocardless`

## usage — follow sepa direct debits, failures and payouts

browse your direct debits, their timeline, failure reasons and grouped payouts for reconciliation.
- `gocardless_payments` lists direct debits (filter by `status`, mandate, customer, date)
- `gocardless_failed` gives you the enriched rejected direct debits in one call (customer, amount, cause, `will_attempt_retry`)
- `gocardless_failure_reason` gives the reason for the latest failure of a specific payment (`PM…`)
- `gocardless_payment_party` resolves payment → mandate → customer (email, company)
- `gocardless_payouts` lists payouts received in the bank (status, currency, reference, dates), amounts in cents
- `gocardless_payout` details a payout (`PO…`) line by line — paid-out payment, failure, fee — to match it against your invoices
