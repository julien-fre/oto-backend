# Post weekly ARR from Stripe to Slack

**When to use it**: you want an ARR figure the team can quote, next to *what changed since the last one*, which is the part anyone acts on. It reads the recurring book in Stripe, adds the prepaid contracts Stripe cannot see as subscriptions, keeps every currency apart until a named rate joins them, and posts one message to your billing channel. Weekly is the default; the same mechanics run daily, because every comparison is against the last run, not the last calendar week.

```
              Scheduled routine, weekly
              "Post this week's ARR to the billing channel."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  Pick the source before counting                ║   stripe_subscription
║  One call decides it: subscriptions if the key  ║   a scope 403 means degraded, not failed
║  allows, subscription invoices if it does not.  ║
╚════════════════════════╤════════════════════════╝
                         ├──────────────▶  ▪ stopped  any other error, nothing posted
                         ▼  a source answered
┌─────────────────────────────────────────────────┐
│  1 · Read the recurring book                    │   stripe_subscription  op=items
│  Every live subscription and its items, plus    │   fallback: stripe_invoice
│  prepaid contracts billed outside Stripe.       │   data_rows  prepaid contracts
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Normalize to one year                      │   two currencies are never added blind
│  Each line to a monthly amount, times twelve,   │
│  tax out and one-offs dropped, per currency.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Convert to one headline figure             │   published daily rate, read at run time
│  Read a published reference rate and restate    │   the rate and its date kept with the snapshot
│  every currency in the headline currency.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Diff against the last run                  │   data_rows
│  Deltas at this run's rate, the FX effect kept  │   no prior row: post a baseline
│  apart, every movement named by customer.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Write the snapshot before posting          │   data_write
│  One row per run and currency, native amounts,  │   keyed on date and currency
│  with the rate used and the per-line detail.    │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  Sanity-check the swing                         ║   a partial read looks like churn
║  A move over 25% since the last run is named    ║   an FX move is not growth
║  by the movements, or flagged as unexplained.   ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Post to the billing channel in Slack       │   slack_post_message
│  The headline alone on line one, then each      │
│  currency, the movements and a watch list.      │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and nothing is posted
```

Two checks sit outside the numbered steps, the source check before step 1 and the swing check before step 6, and both stop the same failure: a confident number built on half a read, or on a rate nobody can point at. Step 1 merges two sources, Stripe and the prepaid-contracts table, before anything is normalized. Every Stripe call in this process is a read.

## Pick the source before counting
- <tool:stripe_subscription> `op="list"`, `status="active"`, `limit=100`, once.
- **It answers**: the source is `subscriptions`.
- **It returns a 403 naming `subscription_read`**: the source is `invoice-fallback`. A billing key is often restricted on purpose, so this is a degraded run, not a failed one, and the post says so every time. Never swap sources silently.
- **Any other error**: stop, post nothing, and say what failed. A missing week is recoverable; a wrong ARR that someone repeats in a board update is not.

## 1. Read the recurring book
- <tool:stripe_subscription> `op="list"` with `status="active"`, then `"trialing"`, then `"past_due"`, paginating on `starting_after` until `has_more` is false. Pass the status explicitly: without it Stripe returns only active and trialing, and a failing payment silently drops out of the book.
- <tool:stripe_subscription> `op="items"` per subscription: that is where `price.unit_amount`, `price.recurring.interval`, `interval_count` and `quantity` actually live. A multi-item subscription contributes the sum of its items, not its first one.
- <tool:stripe_customer> `op="get"` once per customer id, for the name and email every movement line needs.
- Keep on each line: subscription id, customer id, name and email, price id, unit amount, quantity, interval, `current_period_end`, `cancel_at`, `cancel_at_period_end`, `status`.
- **`cancel_at` is a dated end, not a flag.** A fixed-term subscription can be active and already scheduled to stop. It stays in ARR while it runs, sits on the watch list with its date from the first run that sees it, and is reported Gone on the run after it ends. A subscription carrying a `schedule` gets the same treatment.
- **Two live subscriptions on one customer email are a possible double.** Stripe allows several customer records on one address, and a buyer who re-subscribes under a new entity leaves both running. Count both, since both are real subscriptions, and give them their own watch line: "two active subscriptions on `<email>`, same buyer or a duplicate?"

**The invoice fallback**, when the source check came back 403:
- <tool:stripe_invoice> `op="list"` with `created_after` set **[45 days]** back, `limit=100`, paginated, then `op="lines"` per invoice. Keep only lines whose `parent.subscription_item_details.subscription` is set: that field is what separates a recurring line from a one-off.
- For each subscription id keep the **most recent** line, with the amount from `subtotal` (ex tax, before discounts) and the period from `period.start` / `period.end`.
- **What the fallback cannot see, and the post must admit**: a subscription canceled since its last invoice still looks alive; a discount's duration is unreadable when the catalog scope is refused too, so the figure is list price; a subscription whose last invoice predates the window is missed entirely. Widen the window rather than guess.

**Prepaid contracts billed outside a subscription**: a customer who pays several months upfront on a one-off invoice has no subscription, so the recurring filter rightly drops the invoice and the contract vanishes from ARR.
- <tool:data_rows> on your prepaid-contracts table, filtered to `status = "active"`. The table is the **only** sanctioned way such a contract enters the book; never reclassify a one-off invoice as recurring by judgment.
- **Superseded by Stripe?** If this run found a live subscription for the same `customer_id`, skip the row: counting both doubles the customer. Put a watch line asking a person to mark the row superseded. The run never writes to that table.
- **Inside its paid term?** Count the row only while `term_start ≤ run date < term_end`. Once `term_end` passes with no subscription, it stops counting and is reported Gone: a prepaid term nobody renewed is churn. In the **[30 days]** before `term_end`, it goes on the watch list.
- Add it to its currency's book with its `contract_key` as the line id, its list monthly amount ex tax, and `source: "manual-contract"`, with any discount named from the billed amount against list.
- **A converted contract often leaves a credit balance** on the customer, so the new subscription's invoices read zero until it is spent. That is paid revenue, not a discount: ARR carries the subscription's price, and the balance goes on the watch list.

## 2. Normalize to one year
Per line, a monthly amount in the currency's smallest unit:

| Interval | Monthly amount |
|---|---|
| `month` | `unit_amount × quantity ÷ interval_count` |
| `year` | `unit_amount × quantity ÷ (12 × interval_count)` |
| `week` | `unit_amount × quantity × 52 ÷ (12 × interval_count)` |
| `day` | `unit_amount × quantity × 365 ÷ (12 × interval_count)` |

Prepaid-contract lines are already monthly. Then **MRR = the sum of monthly amounts per currency**, **ARR = MRR × 12**, rounded once, at the end, for display only.

- **Never add two currencies blind.** They stay apart to the end of this step, and the per-currency figures are what the snapshot stores. The headline is built in step 3, downstream and reversibly.
- **Only recurring lines count.** Anything with `billing_reason: "manual"` or an invoice-item parent is a one-off (a consulting day, a setup fee, a credit) and is excluded. On a small account the one-offs of a month can outweigh the entire subscription book, so test this filter rather than assume it.
- **Tax is excluded; discounts are shown, not applied.** Keep list price in the figure and name the discount on the watch list, unless its `duration` is `forever`, in which case it belongs in the figure. <tool:stripe_catalog> `op="get_coupon"` reads the duration; try it before declaring one unknown.
- **`active` makes the headline, `trialing` gets its own line.** A trial is not revenue yet. `past_due` and `unpaid` stay in ARR, because the contract exists, but land on the watch list, as does anything with `cancel_at_period_end: true`.

## 3. Convert to one headline figure
The headline is one number in **[your reporting currency]**, first in the post. It is a derived view, never the stored truth: it can always be rebuilt from the per-currency figures and the rate.
- Read a published daily reference rate at run time, with the platform's web page reader (web_read). For a euro headline, the ECB file at `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` is euro-based: its `USD` entry is how many dollars one euro buys, so a dollar amount divides by it. The reporting currency is 1 by definition and is never converted.
- **The rate has its own date.** The ECB file publishes around 16:00 CET on business days, so a morning run reads the previous business day's rate. That is deliberate: a fixed, published, checkable rate beats a fresher one nobody can reproduce. The post states the rate's date, not the run's.
- **When the rate cannot be read**: reuse the most recent `fx_rate` stored in the snapshots, name its date and mark the run degraded. No stored rate either: post the per-currency lines with **no headline**, and say why.
- **Never take a rate from memory, a search result or an average.** If it is not one of the two sources above, there is no headline.
- **Consolidated ARR = Σ (per-currency ARR ÷ that currency's rate)**.

## 4. Diff against the last run
- <tool:data_rows> on the snapshots table, ordered by `run_date` descending, and take the latest row per currency from **before** this run.
- **No prior row**: a baseline. Say "first snapshot, nothing to compare against" and skip the movements, rather than printing +100%.
- **A prior row**: per currency, the delta in absolute and percent, then the movements, matched on line id between the two `lines` arrays: **Started** (new id), **Expanded / Contracted** (same id, different monthly amount, with the plan change named), **Gone** (id no longer present). Name the customer and the monthly amount on every movement; a movement without a name helps nobody in the channel.
- **A prepaid contract replaced by a real subscription** is neither Started nor Gone: match on `customer_id` before declaring churn, and report one line, **Moved into Stripe**, with any difference in monthly amount. The first run that sees a prepaid contract reports it Started, marked as billed outside subscriptions.
- **A new subscription on an email that already had one** is still Started, since it is new money until someone says otherwise, but it carries the duplicate watch line, so nobody reads the jump as clean growth.
- **The headline delta is computed at constant rate.** Convert *both* runs' per-currency figures at **this** run's rate, then subtract. Otherwise a currency move prints as growth or churn that no movement explains, and the sanity check fires on nothing.
- **The FX effect is reported apart**: last run's book at this run's rate minus last run's book at last run's rate. It gets its own line when it exceeds **[1%]** of consolidated ARR, and is dropped silently when it does not.

## 5. Write the snapshot before posting
- <tool:data_write> one row per currency, keyed on `snapshot_key = "<YYYY-MM-DD>|<currency>"`, so a same-day re-run updates instead of duplicating.
- Fields: `run_date`, `currency`, `mrr_cents`, `arr_cents`, `active_count`, `source` (`subscriptions` or `invoice-fallback`), `fx_rate` (units of this currency per headline unit), `fx_rate_date`, `fx_source` (published or carried forward), `lines` (the per-subscription detail the next diff reads, prepaid lines included), `notes`.
- **Store native amounts, never converted ones.** Stored conversions make the next constant-rate diff impossible and bake a rate into history where it cannot be corrected.
- **Write before posting.** If Slack fails, the run is still recorded and the next diff is still right.

## Sanity-check the swing
- A move over **[25%]** in either direction since the last run gets one line saying what drove it, drawn from the movements. If the movements do not explain it, the post says the figure is unexplained and asks for a look: a large drop with no named churn is far more likely a partial read than a lost quarter.
- Because the headline delta is at constant rate, FX can never be the answer here. If it looks like it is, the constant-rate rule was not applied.
- **A single currency's book moving past the threshold gets the same line even when the headline does not.** One small currency doubling is exactly how a duplicate subscription enters the figure unremarked.

## 6. Post to the billing channel in Slack
- <tool:slack_post_message> once, to your billing channel, in Slack mrkdwn. No thread, no @here. Long text is split into threaded parts rather than truncated, and the response carries `split_into` when that happened: read it before concluding anything is missing, because a message can be deleted but not edited, and a re-post is a duplicate.

```text
*ARR: <headline>*
<+Δ / +Δ% since <date of last run>, at today's rate>

• *<CUR>*: <ARR> ARR · <MRR> MRR · N subscriptions (incl. M prepaid outside Stripe)   (<Δ since date>)
• *<CUR>*: <ARR> ARR · <MRR> MRR · N subscriptions   (flat since <date>) → <headline amount>
_Converted at the <source> reference rate of <rate date>: 1 <headline> = <rate> <CUR>._

*Movements*
• Started: <Customer> · <plan> · <amount>/mo
• Expanded: <Customer> · <old plan> → <new plan> · +<amount>/mo
• Moved into Stripe: <Customer> · <plan> · <amount>/mo
• Gone: <Customer> · <plan> · −<amount>/mo

*Watch*
• <Customer>: invoice open / payment failing / cancels at period end / discount ending
• <Customer>: subscription ends <date> (cancel_at); prepaid credit <balance> left
• <Customer>: prepaid term ends <date>, create the subscription or renew
• <Customer>: two active subscriptions on <email>, same buyer or a duplicate?
• FX: <currency> moved <±X%>, worth <±amount> of the headline
```

- **The headline is alone on the first line.** No MRR, no count, no date, no caveat shares it; Slack's timestamp already carries the run date. Everything that qualifies it sits underneath, where the two dates that mean something stay explicit: the comparison date ("since <date>", never "since last week") and the rate's own date.
- **The rate line is never optional and never shortened** to "converted": a number whose rate is not on screen is a number nobody can check.
- The prepaid clause appears only when at least one prepaid contract is counted, and its watch line appears on every post while it is. Each watch line appears only while it is true, and names the customer.
- **A degraded run puts its warning block at the bottom**, naming the missing scope and the fix, so the figures stay readable and the caveat stays attached.

## Rules
- No writes to Stripe of any kind: no invoice, no draft, no coupon, no cancellation. Every call here is a read.
- No writes to the prepaid-contracts table: a row is added or superseded by a person who knows the contract.
- No number posted without its source named (full read or fallback), and no converted figure without its rate and that rate's date.
- A currency the book has never carried before is announced on the first run it appears, not silently converted and folded in.

## Output
One Slack message per run: the consolidated headline alone on its first line; the delta at constant rate with the comparison date; one line per currency with ARR, MRR, subscription count and its own delta; the rate line with the rate's date; the movements (Started, Expanded, Contracted, Moved into Stripe, Gone) with customer and amount; the watch list; an FX line when it matters; and a warning block when the run was degraded. Behind it, one snapshot row per currency that the next run diffs against.