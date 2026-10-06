## prerequisite — the email and password of your planity pro account

oto logs in to [pro.planity.com](https://pro.planity.com) with your credentials, just as you would yourself. enter the **email** and **password** of your planity pro account — they are encrypted in the vault, never returned in clear, and used only to open the session.
- you need an active planity **pro** account, attached to at least one salon (calendar + till)
- the account is personal: everyone sets their own, and sees only the salons planity opens to them
- **a management account opens SEVERAL salons, a salon account just one**: the account you set decides, and `planity_list_salons` tells you which — if you expected four and see only one, it is the account, not the tool
- "test the connection" opens the session and lists your salons — if no salon comes back, the account authenticates but is attached to nothing

## usage — read your calendar, your customers and your figures

read-only. no appointment is created, modified or cancelled.
- `planity_list_salons` to start: the returned `id` is the `salon_id` of all the other tools
- calendar — "which appointments do I have this week?" (`planity_list_appointments`, presets `today` / `this_week` / `30d`…), the detail of one appointment (`planity_get_appointment`), recurring appointments (`planity_list_recurring_appointments`, which appear in NO by-date list)
- customers — search by name, phone or email (`planity_search_customers`), record (`planity_get_customer`), a customer's statistics and receipts (`planity_get_customer_stats`, `planity_get_customer_receipts`)
- reference data — the team (`planity_list_employees`), the catalogue of services and products (`planity_list_services`, `planity_list_products`)
- figures — "what is my revenue this month?" (`planity_get_revenue_summary`), day by day (`planity_get_daily_revenue`), the services/products breakdown (`planity_get_revenue_breakdown`), by staff member (`planity_get_seller_stats`), by payment method (`planity_get_revenue_by_payment_method`), by VAT rate (`planity_get_revenue_by_vat`), by service (`planity_get_service_stats`), the occupancy rate (`planity_get_occupancy_rate`) and reviews (`planity_get_reviews_stats`)
- till, down to the receipt — till sessions (`planity_list_pos_periods`), a session and its receipts (`planity_get_pos_period`), a receipt in detail (`planity_get_receipt`), the table of payment methods (`planity_list_payment_methods`)
- stock — what moves (`planity_list_stock_movements`), suppliers (`planity_list_suppliers`), restocking orders (`planity_list_product_orders`), grouped removals (`planity_list_mass_stock_removals`)
- clientele — best customers, new customers, visit frequency (`planity_get_best_customers`, `planity_get_new_customers`, `planity_get_customer_frequencies`)

## note — what the connector authenticates with

the connector authenticates with the **email and password** of your planity pro account, and nothing else: it does not use the planity application's administrator code.
- what the connector can read is therefore what this account can read — the account, and it alone, defines the perimeter
- to restrict what oto sees, use a planity account with a narrower perimeter
- everything is read-only: no appointment is created, modified or cancelled

## note — this connector requires instance configuration

in addition to your credentials, the connector needs three **planity application endpoints** set once by the operator of the oto instance: `firebase_api_key`, `firebase_app_id`, `rest_api` (connector settings, platform scope).
- if they are missing, the `planity_*` tools stay visible but refuse and say so, naming the missing key and the command that sets it — your credential is then not at fault, and there is nothing to set again on your side
- **they are not secrets**: they are public by design (any browser that opens `pro.planity.com` receives them), they belong to planity, and they authorize nothing on their own — what authorizes is your password, which lives in the encrypted vault. they can appear in an error message or a debug log without being a leak
- if they are not in the code, it is because the client is published as open source: a connector there describes a protocol, it does not embed a third-party company's endpoints as if it were its official integration

## note — what planity does not return

- the per-staff breakdown of `planity_get_revenue_breakdown` (`by_seller`) comes back empty on planity's side, even when passing it the team. the right answer is `planity_get_seller_stats`, which goes through another endpoint — an empty `by_seller` is therefore not a salon with no sales
- planity's aggregated appointment statistics are not exposed: their call expects a parameter that we have not resolved

## note — what the tools do NOT return about customers

the connector returns the raw data and lets the agent compose, **except on a third party's personal data**. at planity, an appointment and a receipt carry the customer's name, phone, email and address; a receipt adds the comment written about her.
- the calendar and till tools return a **list of chosen fields**, and for the customer an **identifier only** — no name, no contact, no address
- `planity_list_appointments` does not return the appointment's free-text comment either (it commonly contains names); `planity_get_appointment`, called for ONE appointment, returns it
- for the person behind an identifier: `planity_get_customer`. it is her record, you asked for it, and nothing comes out until you ask
- this is not an oversight: the customer is not in the conversation, she asked for nothing, and her address has no business crossing an exchange to answer "how much did I make yesterday"

## note — order forecast, in three calls

there is **no forecast tool**: the rule (target coverage, supplier lead time, families to restock) is yours. the tools return the facts.
1. `planity_get_revenue_breakdown` over 90 days → the quantities sold per product, in one call (`by_product`, `bucket_id` = the product's catalogue id)
2. `planity_list_products` → each product's stock and its **purchase lots** (with their purchase price, hence the margin)
3. the calculation is yours: `coverage = stock / (sales ÷ 90)`, to compare with your restocking lead time. `planity_list_stock_movements(product_ids=[…])` gives the detail of the movements on the products that are running out
- ⚠️ `planity_list_stock_movements` **requires `product_ids`**: movements are read one product at a time, and it does not sweep a whole catalogue by itself. without the ids, it refuses at once, reading nothing
- ⚠️ `stock_threshold` and `stock_ceiling` are `null` when the salon does not use them — **`null` is not `0`**: a rule that read zero would order everything, all the time
- ⚠️ a stock drop without a sale is not an anomaly: look at `planity_list_mass_stock_removals` (inventory, breakage, expiry)

## note — deleted is not absent

planity keeps what is deleted: a departed staff member keeps her calendar and past appointments, a withdrawn service stays on old receipts.
- `planity_list_employees`, `planity_list_services` and `planity_list_products` **discard deleted items by default** — a three-person salon does not announce seven
- `include_deleted=true` for history: finding the service on an old receipt, or the revenue of a departed staff member
- all calendars are read for an appointment history, including those of deleted staff members: discarding them would make their revenue vanish with nothing to flag it
- a calendar child is not always a person (booth, workstation, resource): `type` and `title` are returned as planity stores them

## note — read a period before projecting anything from it

`planity_get_revenue_summary` and `planity_get_daily_revenue` return a `period` block: bounds, number of days, timezone (europe/paris), and above all `ends_today` / `complete`.
- most presets stop at **now**, not at the end of the day: the last day is partial
- a daily rate computed over such a window is therefore too low, and a projection built on it ("at the current rate, N days remain") comes out wrong with nothing to flag it
- days with no takings are **absent** from the series, not present at zero: `days_with_revenue` is not `period.days`
