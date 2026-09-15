# Chase failed GoCardless payments with reminder emails

**When to use it**: you collect by direct debit, and every failed payment needs a reminder that matches why it failed — without chasing a debit the provider is about to retry, a debt the customer already settled another way, or the same incident twice.

```
              Natural language input in Claude
              "Chase this month's failed direct debits."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · List the failures                          │   gocardless_failed · data_rows
│  Failures in the window, minus every payment    │   gocardless_payment
│  the log has already closed.                    │   gocardless_failure_reason
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already handled   a final outcome is logged
                         ├───────────────▶  ▪ retry pending     the provider re-presents it
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Confirm it is still owed                   │   pennylane_invoice · pennylane_customer
│  Match the invoice, and stop if it was paid,    │   gocardless_payment_party
│  credited or collected again since.             │   gocardless_payments · gocardless_payment
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ settled           paid, credited or re-collected
                         ├───────────────▶  ▪ needs a person    two open invoices match
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Sort by what failed                        ║   gocardless_payments
║  Low funds, a dead mandate and a dispute each   ║   slack_post_message
║  call for a different next move.                ║   data_write
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ escalated         dispute or repeat failure, posted
                         ├───────────────▶  ▪ needs a person    cause not mapped to a group
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Skip what was already chased               │   gmail_message
│  One reminder per failed payment, checked       │
│  against drafts and sent mail.                  │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already chased    a reminder exists for it
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Draft, then a person sends                 ║   gmail_compose
║  Firm, courteous drafts wait in the mailbox;    ║
║  nothing leaves until someone sends it.         ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Log every payment                          │   data_write
│  Keyed on the payment id, so the next run       │
│  never chases the same incident twice.          │
└─────────────────────────────────────────────────┘

▪ terminal — nothing is drafted for that payment, and its outcome is in the log
```

## 1. List the failures
- <tool:gocardless_failed> with `limit=500` set explicitly — one call returns one row per failed payment: customer name and email, amount, charge date, failure time, cause and reason code, whether a retry is scheduled (`will_attempt_retry`), and the mandate's state. The payment → mandate → customer chain is resolved server-side, so there is no per-row lookup to do.
- **`since` filters on when the payment was created, not when it failed.** A payment created before your window that failed inside it will not come back. Set `since` one full billing cycle earlier than the window you care about, then keep only the rows whose failure time falls inside it. 500 is the largest page and the default is smaller, which is why the limit is passed: if a page still comes back full, narrow the window instead of assuming you have everything.
- <tool:data_rows> — read the reminder log (step 6) once, here, before anything else runs, and index it by payment id. **A payment whose logged outcome is final (*settled*, *escalated*, *needs a person*, *already chased*, *drafted*) is dropped now, before step 3 can post anything about it.** Runs overlap by a full billing cycle on purpose, so without this the same dispute comes back and is posted to your billing channel on every run. Only *retry pending* rows are evaluated again.
- **A retry is scheduled → draft nothing.** While `will_attempt_retry` is true the provider re-presents the payment on its own; a reminder now asks the customer to fix something that may fix itself within days. Log it as *retry pending* (step 6) and move on.
- **Carry over what an earlier run left pending.** For every *retry pending* row in the log, re-read the payment by id with <tool:gocardless_payment>: its creation date may now fall outside the new `since` window, so this is the only way a retry that failed too gets picked up instead of silently dropped. Confirmed or paid out → *settled*. Failed again → <tool:gocardless_failure_reason> for the latest cause and whether yet another retry is scheduled.

## 2. Confirm it is still owed
Resolve the invoice through a fallback chain, strongest key first:
- <tool:pennylane_invoice> with `op=find` and the payment id as `external_reference`. The search is a server-side filter, so an old or archived document still turns up, and a miss comes back as `found: false` rather than an empty list — an upstream outage raises instead of reading as "no invoice". This key only exists if your billing flow writes the payment id onto its documents: test it on one known payment before trusting a miss.
- **A hit is not always the invoice.** The same lookup is the usual duplicate check before raising a credit note against a failed payment, so the document carrying the payment id is often that credit note. Read what came back: a credit note (credit notes carry negative amounts) means the debt was already cancelled, and the payment ends here as *settled (credited)*. Only a sales invoice with a positive amount goes on to the paid check below.
- On a miss: <tool:gocardless_payment_party> — the payment's metadata may carry your own customer reference (depending on how the mandate was created, it may not). Then <tool:pennylane_customer> `op=find` on that reference, or `op=list` to resolve the customer id from the name when there is no reference.
- Then <tool:pennylane_invoice> `op=list`, **always bounded with `max_pages`** — without it the whole invoice history comes back and can overflow the response. Start small and widen. Match sales invoices only, on the customer, the amount and a due date close to the charge date. **Two open invoices match → pick neither**: log the payment as *needs a person* rather than chasing the wrong document.

Then two checks that end the payment here as *settled*:
- The matched invoice already shows as paid — a bank transfer came in after the debit failed. This is the check to trust.
- <tool:gocardless_payments> with the `mandate` filter and `since` set to the failed charge date, no `status` filter, keeping confirmed and paid-out rows of the same amount. **On a subscription, that row is usually just the next cycle**, and because step 1 reaches back a full cycle it will often already exist; reading it as a re-collection drops a real debt without a warning. Count it only when <tool:gocardless_payment> shows no subscription or installment schedule under `links`, and, when your billing flow writes one, its `reference` or `metadata` points at the same invoice. Otherwise ignore it and rely on the invoice's paid status alone.

## 3. Sort by what failed
Map the cause and reason code from step 1 into groups once, then treat each group the same way every run:
- **Low funds, no retry left** — the mandate still works. A refer-to-payer refusal belongs here too: the bank declined and sent the payer to ask why, which is usually low funds, not a dispute. The reminder states the amount and the invoice, and gives one next step: the date you will collect again, or your transfer details.
- **Dead mandate** — cancelled, expired, or the bank account closed. Collecting again fails the same way, so the ask is a new mandate through **[your mandate setup link]**, never "we will try again".
- **Dispute** — only a payer disputing the authorization, or a chargeback-type cause. A chasing email escalates it. <tool:slack_post_message> to **[your billing channel]** with the customer, amount, invoice reference and cause, then stop: a person handles it by hand. Logged as *escalated*.
- **Anything unmapped** — invalid bank details, a transferred account, a cause you have never seen. Guessing a group sends the wrong ask, so it becomes *needs a person* and is listed in the run report for someone to handle and map to a group for future runs.
- **Repeat failures** — <tool:gocardless_payments> with `mandate=<mandate id>`, `status="failed"`, `since` at the start of **[your lookback window]** and `limit=500`, then count the rows. Take the mandate id from the step 1 row when it carries one, else from `links` in <tool:gocardless_payment>. Count failed payments, not events: the events feed has no date filter, and its failed action belongs to a payment rather than to the mandate you filtered on. At **[your repeat-failure threshold]** the payment gets the same channel post as a dispute instead of a polite email, logged as *escalated*: a customer who keeps failing needs a conversation, not another reminder.
- <tool:data_write> — right after posting, write the *escalated* rows in one keyed batch (`key` = payment id) instead of waiting for step 6. A run that stops between the post and the end-of-run log would otherwise post the same payment again next time.

Order what remains by amount, then by days since the failure. Step 5 drafts in that order, so the reviewer reads the largest, oldest debts first.

## 4. Skip what was already chased
One incident is one payment id, and it gets one reminder at most. The log already did its part in step 1: nothing it closed gets this far. What is left are the duplicates the log cannot see:
- <tool:gmail_message> `op=search` with `in:drafts subject:"[invoice reference]"` — a draft from an earlier run that stopped after drafting but before step 6 logged it. Search for it rather than listing drafts: the list returns only a page of the most recent ones, and in a busy mailbox the draft you are looking for is not on it.
- <tool:gmail_message> `op=search` with `in:sent subject:"[invoice reference]"` — a reminder someone wrote by hand, outside this process.

Either match ends the payment as *already chased*. Both checks only work because the subject line in step 5 always carries the invoice reference. Don't reword it run to run.

## 5. Draft, then a person sends
- <tool:gmail_compose> — leave `mode` unset. The default saves a **draft**; `mode="send"` is what actually sends, and this process never sets it. Read `kind` in the response and report what happened: *draft*, never *sent*.
- **To**: the invoice recipient on the accounting side when it differs from the payer's email on the debit — whoever receives invoices is usually the one who can fix a payment. **Subject**: a fixed pattern with the invoice reference, e.g. *Payment failed — invoice [invoice reference]*. **Body**: amount, invoice reference, charge date, and the one next step from step 3. Firm and courteous; no late-payment penalty unless your terms state one.
- A new draft can be missing from a Gmail tab that is already open, for a while. The write is fine: tell the reviewer to reload or search the drafts folder rather than assume it failed.

## 6. Log every payment
- <tool:data_write> — one batch call at the end of the run, with `key` set to the payment id, so a re-run merges onto the same rows instead of appending duplicates. One row per payment this run evaluated, **including the ones that exited early**: payment id, invoice reference, cause group, outcome (*retry pending*, *settled*, *settled (credited)*, *escalated*, *needs a person*, *already chased*, *drafted*), the Gmail draft id when there is one, and the run date fetched at run time rather than typed in. Write it to **[your reminder log]**.
- Payments dropped in step 1 because their outcome was already final stay out of the batch, so a *drafted* row is never overwritten by a weaker outcome on a later run.

## Output
A short report: failures found in the window, then how many were skipped as already handled, retry pending, settled (paid, credited or collected again), escalated to your billing channel (disputes, repeat failures), already chased, or left for a person (ambiguous invoice match, unmapped cause), and how many drafts are waiting in the mailbox, listed largest first with customer, amount and cause.

## What this never does
- Never collects a payment again, creates a credit note, or finalizes or sends an invoice — the accounting side is read-only here.
- Never sends an email itself: every reminder is a draft a person sends.
- Never emails a customer about a disputed debit.