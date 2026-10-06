## prerequisite — stripe api key

create a **restricted key** in Stripe (Dashboard → Developers → API keys → "Create restricted key" — see the [keys doc](https://docs.stripe.com/keys)), then paste it into oto.
- **read** permissions are enough to query customers/subscriptions/invoices/payments/balance — **none of that moves money**. writing to the catalog (products, prices, coupons, promotion codes, invoice items, payment links: **Payment Links: Write**) additionally requires the matching **write** scopes on the restricted key (e.g. Products/Prices/Coupons/Promotion codes write) — without them, Stripe refuses those writes with a 403
- a restricted key (`rk_…`) is preferable to a secret key (`sk_…`): it limits what the key can reach even if it leaks, and Stripe explicitly recommends it for AI agents
- ⚠️ a **publishable** key (`pk_…`) is refused: it is the browser token, it can read neither customers nor invoices
- the **mode** is read from the key: `rk_test_…` / `sk_test_…` = test mode, `rk_live_…` / `sk_live_…` = live mode. The two worlds are separate — a test customer does not exist in live mode, and vice versa
- byo-only: no shared oto key. these are your books

two optional fields next to the key:
- **API version** — leave empty unless you have a specific reason: empty = your account's default version, the one your dashboard shows
- **connected account** (`acct_…`, Stripe Connect) — if you fill it in, **all** reads target that account and not your own

## usage — customers, subscriptions, invoices, collections, balance

- "how much did we invoice last month?" → `stripe_invoice(op="totals", created_after=…, created_before=…)` (sum **per currency**, with a `complete` flag)
- "how much did we actually collect?" → `stripe_balance(op="transactions", …)` — each line carries its fees and its net, which invoices do not know
- "how much do we have in the bank?" → `stripe_balance(op="get")`
- "when does the next payout arrive?" → `stripe_balance(op="payouts")`
- "find this customer" → `stripe_search(resource="customers", query='email~"acme.com"')`
- "their invoices" → `stripe_invoice(op="list", customer_id="cus_…")`
- "who is about to cancel?" → `stripe_subscription(op="list", status="all")` then filter on `cancel_at_period_end`, `past_due`, `unpaid` — Stripe has no "on the way out" state
- "why did this payment fail?" → `stripe_payment(op="get_intent", payment_intent_id="pi_…")` and read `last_payment_error`; or `op="get_charge"` and read `outcome.seller_message`
- "is their card expired?" → `stripe_customer(op="payment_methods", customer_id="cus_…")`
- "do we have any disputes in progress?" → `stripe_payment(op="list_disputes")` — watch `evidence_details.due_by`, the money is already withdrawn from the balance in the meantime
- "make me a payment link for this offer" → `stripe_catalog(op="list_prices")` then `stripe_checkout(op="create_link", price_id="price_…")`
- "subscription link to pay only once, attached to a given account in my application" → `stripe_checkout(op="create_link", price_id="price_…", created_metadata={"account_id": "…"}, max_uses=1)` — `created_metadata` is set on the subscription (recurring price) or the payment (one-time price) created at checkout, because Stripe does not copy the link's `metadata`; it requires **Prices: Read** on the key. `max_uses=1` deactivates the link after the first payment
- "add €200 to their next invoice" → `stripe_invoice(op="add_item", customer_id="cus_…", amount=20000, currency="eur")`
- "create a -20% code for the launch" → `stripe_catalog(op="create_coupon", percent_off=20, duration="once", name="LAUNCH20")` (returns a `coupon_id`) then `stripe_catalog(op="create_promotion_code", coupon_id="cp_…", code="LAUNCH20")` — the coupon is the discount RULE, the promotion code is the TEXT the customer types
- "deactivate this promo code" → `stripe_catalog(op="update_promotion_code", promotion_code_id="promo_…", active=false)` — redemptions already made are not affected, only future uses are blocked
- "which promo codes are active?" → `stripe_catalog(op="list_promotion_codes", active=true)`

## note — what this connector will never do

refund, cancel a subscription, finalize/send/collect an invoice, transfer money, close a dispute, delete a customer: **none of these operations is reachable**, and not merely by configuration choice — the corresponding methods do not exist in the underlying library. do them from your Stripe dashboard, where they are traced and confirmed.

what remains possible in write mode is deliberately free of direct financial consequence: create/update a customer, put a line on a next invoice, create a **draft** invoice, manage the products/prices/coupons/promotion codes catalog, create a payment link (page hosted by Stripe — no card number goes through oto, and nobody is charged until a human has paid). a coupon/promo code does nothing by itself: it only applies when a customer pays through a link/checkout that accepts it, or when you attach it yourself to a subscription from the dashboard.

## note — traps verified live on 2026-08-22 (invoice/link/price) and 2026-08-23 (coupons/promotion codes)

- **a draft invoice does not pick up pending lines unless asked.** putting a line down then creating the invoice returned `total=0` and zero lines, the amount left hanging — a perfectly false "invoice created". `stripe_invoice(op="create_draft")` therefore passes `pending_items="include"` by default; set `"exclude"` if you really want an empty draft
- **a payment link may require a tax code on the product** (accounts eligible for managed payments): `400 "the product tax code is missing"`. fix → `stripe_catalog(op="update_product", product_id=…, tax_code="txcd_10000000")` (generic services), then recreate the link
- **the amount of a Stripe price is immutable.** "changing the price" = creating a new price and deactivating the old one (`op="update_price", active=false`); `update_price` explicitly refuses a `unit_amount`
- **creating a promotion code changed shape on recent accounts** (verified on a real account on API version `2026-07-29.dahlia`, the default version of a new account): Stripe removed the flat `coupon` field in favor of a nested `promotion` object. This is handled internally (`coupon_id` remains the parameter on the `stripe_catalog` side), nothing to change on the usage side — but if you read a promotion code's raw response, the applied coupon is under `promotion.coupon`, no longer under `coupon`
- **a promotion code's `expires_at` cannot exceed the `redeem_by` of its coupon** — Stripe flatly refuses, naming both timestamps, if you set a code expiry later than the coupon's own deadline

## note — read limits

- lists return **100 objects at most** per page, and Stripe silently falls back to 10 if nothing is requested — the connector always sets 100. there is no total on top-level lists: for a figure, `op="totals"`, not a hand-made sum
- `op="totals"` sweeps up to 2,000 invoices and **says so** (`complete: false`) beyond that, instead of returning a partial sum that would pass for revenue
- **amounts are in the smallest unit of the currency** (cents) and are **never added across currencies**
- search (`stripe_search`) covers only seven resources and is eventually consistent: an object created just now may only appear after a minute
- `stripe_event` only goes back **30 days** (Stripe retention) — for a quarter, go through invoices and payments
