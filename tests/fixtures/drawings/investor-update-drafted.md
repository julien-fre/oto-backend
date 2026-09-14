# Draft the monthly investor update

**When to use it**: the monthly update is mostly assembly (billing dashboard, pipeline export, the plan, the variance, what the last update promised), so it slips a week and goes out thinner than intended. This run does that assembly from the systems of record in the month's first week and stops at a Gmail draft where every figure is sourced and every gap visible. The founder writes what the numbers mean and sends it; nothing in the draft is estimated, because investors will hold you to every figure.

```
              Scheduled routine, each working day of the month's first week
              "Draft last month's investor update from the numbers."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the month is ready to draft          ║   stripe_invoice
║  Fix the range in your timezone; stop while     ║   gmail_message
║  billing is unsettled or once drafted.          ║   sent or drafted update ends the run
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ not closed        Stripe still finalizing the month
                         ├───────────────▶  ▪ already drafted   this month's update already exists
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Add up revenue actually collected          │   stripe_subscription
│  New, lost and closing recurring revenue, with  │   stripe_invoice
│  one-off revenue kept apart.                    │   stripe_event
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Size the pipeline, flag big deals          │   hubspot_object
│  Opened, won, lost and open by stage; a deal    │   one sales pipeline only
│  that moves the total alone is named.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Measure the gap to plan                    │   sheets_spreadsheet
│  Each target beside its actual; one the plan    │   raw values, month found by header
│  lacks is reported missing.                     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Report on last month's promises            │   gmail_message
│  Every commitment in the previous update, read  │   sent mail only
│  from sent mail, not memory.                    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Draft a fully sourced update               │   gmail_compose
│  Every figure with its source and range; an     │   saved as a draft, never sent
│  unavailable one stays a visible gap.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Keep this month as the next baseline       │   data_write
│  Recurring revenue per subscription and         │
│  pipeline per stage, keyed on the month.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · A human writes the other half              │   the one human step
│  The founder adds what the numbers mean and     │
│  what comes next, then sends it.                │
└─────────────────────────────────────────────────┘

▪ terminal — nothing is drafted this run; a month not yet closed is tried again on the next scheduled day
```

Steps 1 to 3 read independently; step 4's status check and step 3's cause clause use steps 1 and 2; step 5 needs all four. A step that fails does not stop the others: its section of the draft says what could not be read and why, which is the difference between a gap and a guess.

## Before the first run
Keep one config row the run reads with <tool:data_rows>: **[your reporting timezone]**, the Stripe account's currencies, **[your sales pipeline id]** with its stage ids **and their labels**, the plan spreadsheet id, its tab and a map of **[metric → plan row label]**, the subject line your updates go out under, the mailbox they are sent from, and the investor distribution address the draft is addressed to. The stage labels have to live here: <tool:hubspot_property> returns `dealstage` with an empty options list, because stages belong to a pipeline and the connector does not read pipelines. A draft that prints raw stage ids is the symptom.

Schedule it on each working day of the month's first week rather than once. The first run that finds the month closed writes the draft; every later run stops at step 0 because the update already exists, so a month that was still settling on day one gets picked up without anyone rerunning it.

## 0. Check the month is ready to draft
- Fix the range first: the first and last instant of the closed month **in your reporting timezone**, converted to Unix seconds for Stripe and to epoch milliseconds for HubSpot. Stripe and HubSpot both store UTC, so a range built in UTC shifts a few hours of the month into its neighbor for any company not on UTC. Every figure in the draft carries this range, written out.
- <tool:gmail_message> — `op="search"` on the updates mailbox (pass `account`; `gmail_list_accounts` lists the connected ones) with `subject:"[your update subject] · <month and year>" (in:drafts OR in:sent)`. A hit means this month is already drafted or already out: write nothing, report the message id, stop. Searching drafts alone would miss an update the founder already sent, and draft the month a second time. Use the search rather than `op="drafts"`, which takes no query and returns only the latest 20 drafts, so a busy drafts folder can hide this month's.
- <tool:stripe_invoice> — `op="list"` with `status="draft"` and `created_after` / `created_before` on the range.
  - A draft with `auto_advance` true is one Stripe is still going to finalize: the month has not settled, and the run stops until the next scheduled day.
  - One still a draft **[2 working days]** after month end has most likely failed its automatic finalization, and waiting will not resolve it. It stops blocking: the run goes ahead and names it in the draft as excluded from billed revenue, so it cannot hold the update back all week in silence.
  - A draft with `auto_advance` false is a manual invoice someone left open. It does not block the run either, and is named the same way rather than quietly left out.

## 1. Add up revenue actually collected
Revenue comes from Stripe and never from the CRM. A CRM records what was sold, Stripe records what was billed and collected, and failed payments, prorations, mid-month downgrades and refunds all live in the gap.
- <tool:stripe_subscription> — `op="list"` with **`status="all"`**, 100 per page with `starting_after`. Without the status filter Stripe returns only active and trialing subscriptions, and every cancellation of the month silently disappears from a churn figure. For a subscription with more than one line, `op="items"` returns each line's price and quantity, which is where seat counts live.
- **Recurring revenue per subscription**: each line's unit amount times quantity, normalized to a month from the price's interval and interval count (yearly ÷ 12, quarterly ÷ 3), less any discount on the subscription. Count `active`; list `past_due` separately as at risk; exclude `trialing`, which has not paid anything. Write the rule into the draft so next month is measured the same way.
- **Unwind the days since month end.** The list is the state on the run day, not at midnight on the last day. <tool:stripe_event> `op="list"` with `type="customer.subscription.*"` and `created_after` = the end of the range: each `updated` event carries `data.previous_attributes`, so apply them in reverse to get month-end amounts; a `created` event after the range is next month's new revenue; a `deleted` event after the range is still this month's recurring revenue. Stripe keeps events for 30 days only, which covers a run in the month's first week and not one made weeks later.
- **Movement against last month's baseline** (step 6 of the previous run, read with <tool:data_rows>): a subscription absent from the baseline is **new**; one in the baseline whose **`ended_at`** falls inside the range is **lost to cancellation**; one whose amount fell is **lost to downgrade**; one whose amount rose is **expansion**. Cancellations and downgrades stay on separate lines, because they are different problems. An active subscription with `cancel_at_period_end` true has not left yet: it is listed as scheduled to cancel, not counted as lost. Use `ended_at`, not `canceled_at`: on a subscription canceled at period end, `canceled_at` is the day someone asked, which can be months before the revenue actually stopped. On the first run there is no baseline, and the draft reports the closing figure with movement marked "no baseline yet" instead of reconstructing it.
- <tool:stripe_invoice> — `op="list"` with `status="paid"` and `created_after` set **[90 days]** before the range, then keep the invoices whose **`status_transitions.paid_at` falls inside the range**. The list filters on `created` only, and an invoice billed on the 30th and paid on the 2nd is billed revenue this month and collected revenue next month. Split what was collected on the invoice's subscription, read from the top-level `subscription` or from `parent.subscription_details.subscription` depending on the account's API version; check both, or every invoice reads as one-off on a version that moved the field. With a subscription it is recurring; without one it is **one-off revenue, reported on its own line**, because folding a setup fee into recurring overstates the figure investors track most closely.
- <tool:stripe_invoice> — `op="totals"` on the range's `created` dates, one call each for `status="paid"`, `"open"` and `"uncollectible"`, gives billed revenue per currency without paging; drafts and voided invoices are billed nothing. If it answers `complete: false`, the range held more invoices than the sweep reads: split the range in two and sum the halves rather than reporting a partial total.
- **Outstanding** comes from the same `open` and `uncollectible` calls: `amount_due` less `amount_paid`, per currency, labeled "billed in range, still unpaid". It is never billed minus collected: billed is keyed on the invoice's creation date and collected on its payment date, so the two cover different invoices and their difference can be wrong or even negative.
- Amounts come back in the smallest currency unit and grouped by currency. **Never add two currencies together**; report each, and convert only at the rate your plan uses, named in the draft.

## 2. Size the pipeline, flag big deals
- <tool:hubspot_object> — `op="search"` on `deals`, every query filtered on `pipeline` EQ **[your sales pipeline id]**, asking for `dealname`, `amount`, `dealstage`, `createdate`, `closedate`, `hs_is_closed` and `hs_is_closed_won`, 100 per page with `after`. Without the pipeline filter, a renewals or partnerships pipeline quietly inflates the total.
  - **Opened**: `createdate` BETWEEN the range.
  - **Won**: `closedate` BETWEEN the range and `hs_is_closed_won` EQ `true`. **Lost**: `closedate` BETWEEN the range, `hs_is_closed` EQ `true`, `hs_is_closed_won` EQ `false`.
  - **Open by stage**: `hs_is_closed` EQ `false`, summed per stage with the labels from the config row. HubSpot's search stops paging at 10,000 results; a larger pipeline is read one stage at a time.
- The open pipeline has no history in HubSpot, so it is stated **as of the run's timestamp**, and its movement is measured against the per-stage totals the previous run stored. Deals with no `amount` are counted and named separately, never summed as zero.
- **Flag any single deal whose amount alone is at least the month's net movement, or at least [a fifth] of the open total.** An investor reading a large pipeline increase should be told when it is one deal.

## 3. Measure the gap to plan
- <tool:sheets_spreadsheet> — `op="metadata"` for the tab list, then `op="read"` on the plan tab with **`formatted=false`**. Formatted reads return display strings ("12.4k", "8%") that parse wrong; raw values come back as numbers, with percentages as fractions.
- **Find the month's column by its header, never by position.** A column inserted mid-year shifts every position by one and silently compares this month's actuals with next month's plan. Find each metric's row by the label in the config map.
- For each target: actual, plan, the absolute difference and the percentage difference. A plan of zero has no percentage; say so. **A blank cell is "no target in the plan"**, never last month's target carried forward.
- Commentary is one clause, and only where the cause is visible in data steps 1 and 2 already read: "recurring revenue [x]% under plan; two named cancellations in step 1". No adjectives on the numbers. The reader can decide whether a variance is good.

## 4. Report on last month's promises
- <tool:gmail_message> — `op="search"` with `in:sent subject:"[your update subject]"` and an `after:` date about six weeks back, on the updates mailbox. Take the most recent one sent before this month, then `op="get"` for its full body. If the update went out as an attachment, `op="attachment"` returns small text inline and a PDF as a short-lived link.
- Extract every commitment it made: a hire, a launch, a target, a milestone, a date. **Generate this list from the email, never from memory.** It is the section that quietly goes missing in a bad month, not through dishonesty but because memory retrieves the promises that were kept.
- For each commitment, the status the systems can show: a revenue or pipeline target is checked against steps 1 and 2. A hire or a launch cannot be verified here, so it is marked **[to confirm]** for the founder, never guessed as done. The founder then writes done, slipped with a new date, or dropped with a reason.
- If no previous update is found, the section says so, with the query used. An empty list and a failed search must not look alike.

## 5. Draft a fully sourced update
- <tool:gmail_message> — the step 0 search again, just before composing. A draft that appeared since the run started means write nothing and report its id; composing again only creates a second draft.
- <tool:gmail_compose> — **called without `mode`**, which saves a draft, from the updates mailbox, to the investor distribution address, subject **[your update subject] · <month and year>**, exactly the string step 0 searches for. `mode="send"` appears nowhere in this process. Read `kind` in the answer: `draft` means saved. A fresh draft can take a while to appear in a Gmail tab that is already open, so reload or search `in:drafts` before deciding it failed.
- Order: headline numbers, variance against plan, last month's commitments and their status, pipeline, then an empty **[Asks]** section.
- Rules for the body:
  - Every figure carries its source and the exact range: "Stripe, paid invoices, 1–31 [month], [timezone]".
  - Anything unavailable is written as **[not available: reason]**, never estimated. A plausible filled-in number is indistinguishable from a real one on the page, and it becomes the baseline next month is compared against.
  - No adjectives on figures, and no commentary beyond a cause visible in the data.
  - Draft invoices excluded in step 0 and deals with no amount from step 2 are listed in a short note at the end, so the founder can decide whether they matter.

## 6. Keep this month as the next baseline
- <tool:data_write> — one batch into **[your investor-update baseline table]** with `key` = `<YYYY-MM>|<subscription id>`: month-end recurring amount, currency, status and customer. One more row keyed `<YYYY-MM>|pipeline` with the open total per stage and the run timestamp. The key makes a rerun in the same month update its rows instead of doubling them, and next month's step 1 and step 2 read exactly these rows.

## 7. A human writes the other half
The founder opens the draft, confirms the **[to confirm]** statuses, writes what the numbers mean and what comes next, fills the asks, and sends it. That half is the part a founder should be writing, and the run never touches it.

## Output
The exact range and timezone used; closing recurring revenue per currency with new, expansion, cancellation, downgrade and scheduled-to-cancel movement; collected, billed, outstanding and one-off revenue; opened, won, lost and open pipeline by stage, with any deal flagged as moving the total alone; actual against plan for each target, and targets missing from the plan; last month's commitments with the status shown or marked to confirm; every figure that could not be retrieved and why; and the Gmail draft's id. A run that stopped at step 0 reports which exit it took: the unsettled draft invoices, or the id of the update that already exists.