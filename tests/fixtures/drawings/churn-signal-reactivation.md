# Spot falling product usage and email the account

**When to use it**: accounts stop using a product well before anyone says they are leaving, and nobody opens analytics for every account every week to notice. Run this weekly across your paying accounts to separate real declines from holidays and tracking changes, hand close renewals to their owners, and draft one plain question to the person who stopped.

```
              Scheduled routine, weekly
              "Which paying accounts are quietly using the product less, and why?"
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check usage can be read per account        ║   posthog_group, posthog_schema
║  Account groups in analytics, joined to your    ║   hubspot_object, paying companies
║  paying companies on a stable id.               ║   unmatched is reported, never zero
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no account groups  analytics has no group type
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Measure usage in 30-day buckets            │   posthog_query, one HogQL aggregate
│  Last 30 days against the mean of the three     │   not a call per account
│  months before: users, depth and breadth.       │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ steady            no measure fell past the bar
                         ▼  a measure fell past your threshold
╔═════════════════════════════════════════════════╗
║  2 · Set aside explained drops                  ║   posthog_query, project-wide
║  A season, a renamed event or an outage is not  ║   the same window last year
║  churn; cut seats become a contraction.         ║   data_rows, logged seat counts
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ explained         counted by reason, not emailed
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Read the account in the CRM                │   data_rows, raised in recent weeks
│  Skip anything raised lately, then read plan,   │   hubspot_object, hubspot_owners
│  renewal, owner and tickets before the drop.    │   a note to the owner, not an email
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already raised    noted or drafted in recent weeks
                         ├───────────────▶  ▪ owner calls       renewal close, or seats cut
                         ├───────────────▶  ▪ open complaint    a ticket preceded the decline
                         ▼  not raised, no close renewal, no complaint
┌─────────────────────────────────────────────────┐
│  4 · Draft one email naming what stopped        │   hubspot_object, the contact
│  To the person most active before the decline,  │   gmail_compose, saved as a draft
│  with one easy question.                        │   no send mode anywhere
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · A human reads every draft                  │
│  Nothing reaches a paying customer until a      │
│  person has read and sent it.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Log each account for the week              │   data_write, keyed on account and week
│  One row per account per week, with its seat    │
│  count, so nobody is raised twice too soon.     │
└─────────────────────────────────────────────────┘

▪ terminal — no email is drafted for that account; it is still logged and counted
```

## 0. Check usage can be read per account
- <tool:posthog_group> — `op="types"` first. It returns the project's group types and their index, and no group call or group query is possible without that index. **An empty result means the project does no group analytics**, and account-level questions have no answer there. Stop and say so rather than silently answering per person: a list of people using the product less is not a list of accounts at risk.
- <tool:posthog_schema> — `op="columns"` with `table="events"` to read the exact name of the group-key column for your account type's index, and `op="events"` for the event types actually seen. Write every query in step 1 from this, not from memory.
- <tool:hubspot_object> — `op="search"` on companies for **[your paying-customer filter]** (a lifecycle stage, a plan property), 100 per page with `after`, asking for **[the property that stores your product's account id]** and **[your seat-count property]**. The seat count is the only CRM value read before step 3, and it comes free with this search. The account id is the join key to the analytics group key. Match on it, never on company name: two spellings of one name look like two accounts, and a join that misses reads as an account whose usage fell to zero.
- **A paying company with no matching group is reported as unmatched, never as zero usage.** A broken join that turns into a "100% drop" is the most damaging false positive this process can produce, because it points a reactivation email at a healthy customer.

## 1. Measure usage in 30-day buckets
- <tool:posthog_query> — one `hogql` query over the last 120 days, filtered to the matched keys from step 0, grouped by the group key and a 30-day bucket, `intDiv(dateDiff('day', timestamp, now()), 30)`. Bucket 0 is the last 30 days, and buckets 1 to 3 are the baseline. It is one aggregate query, not a call per account. Per account and bucket:
  - **Active users**: `uniq(person_id)`. Never `count(distinct distinct_id)`, which counts devices, not people. A fall here is an adoption problem.
  - **Events per active user**: the bucket's events over its active users. A fall here means the people still using the product are doing less with it, which is usually worse.
  - **Breadth**: `uniq(event)` over **[your meaningful product events]**. Narrowing to a single feature is the clearest early signal there is.
- **The baseline is the mean of the three monthly buckets, never a 90-day figure divided by three.** Unique users and distinct events do not add up across months, because the same people and the same features come back every month. A 90-day `uniq` divided by three shrinks the baseline, so real drops in exactly these two measures never cross the threshold, and nothing says so. An outer query folds the buckets back into one row per account with `sumIf` over buckets 1 to 3, divided by three, so a month with no activity counts as zero instead of dropping out of the average. The baseline events per active user is the baseline event mean over the baseline active-user mean, which never divides by an empty month.
- Leave `$pageview`, `$pageleave`, `$autocapture` and the other `$`-prefixed system events out of depth and breadth. They measure browsing, and a redesign moves them without anyone changing how they work.
- PostHog caps an unbounded query at 101 rows and sets `hasMore`. Order the outer query by the group key and page with `LIMIT` and `OFFSET`, keeping the aggregation in the query, rather than pulling events back to count them yourself.
- **Why the last 30 days against the three months before.** Thirty days against the previous thirty turns every August and every late December into a churn alert, and after two of those nobody reads the list. The longer baseline absorbs a holiday and still catches a genuine three-month slide, which is the shape real churn has.
- **Flag an account** when any of the three measures fell by more than **[30%, or your own threshold]** and its baseline is above **[your minimum baseline]**. Below that floor the percentages are noise.
- **Keep the three measures apart.** An account whose event count is stable but which has narrowed from several features to one is in more trouble than one that is down a little across the board, because it now depends on one thing it could replace. Averaged into a single health score, that account looks fine.
- For the flagged accounts only, a second query returns weekly event counts across the 120 days and the person with the most events in the baseline window, with `person.properties.email`. The first week that falls below the baseline average dates the decline, and steps 3 and 4 both need that date.

## 2. Set aside explained drops
Before a drop counts as a signal, rule out the explanations that are not churn. Anything explained here is counted by reason and set aside, never emailed.
- **A renamed or re-instrumented event.** A drop confined to one event that fell for **every** account in the same week is a tracking change, not abandonment. Check it with one project-wide <tool:posthog_query> of weekly counts per event, not a query per account: an event falling to near zero in the same week another name starts rising is a rename. `posthog_schema` `op="events"` only lists the event types seen, not when each first appeared, so use it to confirm the new name exactly as tracked, never to date it. Take the renamed pair out of the measures, or count them as one, and evaluate the account again.
- **An outage.** The same project-wide query shows a dip across all accounts on the same days.
- **Seasonality.** Where the project holds more than a year of data, compute the same ratio for the same calendar window last year. A matching dip last year explains this one. Where it does not hold a year, record "no seasonal baseline" rather than assuming either way.
- **Removed seats.** The CRM only returns a property's current value, so the seat history comes from this process's own log. <tool:data_rows> — one read of the log filtered to the flagged accounts over the last 120 days, reused in step 3. Compare the seat count from step 0 with the one logged for the same account in the first week of the baseline window. Fewer active users while the seat count fell is a contraction, a different conversation from disengagement: the account is marked as one and carried into step 3, where it becomes a note to the owner, never a reactivation email.
- **Until the log reaches back that far**, use what the CRM can say today: a current seat count below the baseline's average active users means seats were cut. Anything else is recorded as "no seat history yet" and the account goes on.

## 3. Read the account in the CRM
- **Already raised comes first, before any write.** From the log rows read in step 2 (<tool:data_rows>), an account with a note to its owner or a draft in the last **[four weeks]** is listed as "still declining, already raised" and gets neither a CRM note nor a draft this week. Checking later would let an account with a close renewal collect a fresh note on its record every week until the renewal.
- <tool:hubspot_object> — `op="get"` on the company with **[your plan property]**, **[your renewal-date property]**, the company owner and `notes_last_contacted`, plus `associations=["tickets","contacts"]`. That returns the ticket and contact ids inline in the same call instead of one associations call per type.
- <tool:hubspot_owners> — once per run, mapping the owner ids on company records to names and emails. Do not call it per account.
- <tool:hubspot_object> — `op="search"` on tickets with `hs_object_id` `IN` the associated ids, 100 per call, for `subject`, `createdate`, `closed_date` and `hs_pipeline_stage`.
- **A renewal inside [ninety days] is not an email.** <tool:hubspot_object> `op="add_note"` on the company for the owner to call: which measure fell, by how much against the baseline, since which week, and the renewal date. A note notifies nobody on its own, so the run report also lists these accounts by owner. The account stops here.
- **An unresolved complaint before the decline is very likely the cause.** A ticket created in the **[few weeks]** before the decline week and either still open or closed only after the decline began goes to the owner the same way, with the ticket named plainly. No reactivation email is drafted: an email asking "what changed" when they already told you is worse than silence.
- **A contraction from step 2** goes to the owner the same way: which seats went, since when, and what the remaining users still do. The account stops here.
- Keep `notes_last_contacted` for the reviewer in step 5. A draft to someone a colleague spoke to this week needs a different first line, or none at all.

## 4. Draft one email naming what stopped
- **The recipient** is the person most active before the decline (step 1), confirmed as a contact on the company with one <tool:hubspot_object> search on contacts by `hs_object_id` `IN` the associated ids, rather than the billing contact. The person who stopped doing something is the one who can say why. The billing contact usually has no view of daily use and forwards the question, and the specifics are lost on the way.
- **If that person's activity stopped entirely while their colleagues carried on**, the likelier story is that they left the account. Put that in a note to the owner instead of writing to an inbox nobody may read.
- **Name the specific behavior** and the week it stopped, in your customers' words rather than the event's: keep a short map of **[event name → what customers call it]** so a draft never quotes `report_exported` at a customer. "You have not used the weekly export since [month]" is a sentence only someone reading the data could write, and it invites a real answer. "Your usage has declined" invites nothing and reads as automated, because it is.
- **Then one question** about what changed, cheap to answer. No discount, no feature pitch, no calendar link. The email is a question, not a save attempt.
- <tool:gmail_compose> — call it without `mode`, which saves a draft, and pass `account` for the mailbox the reviewer works from. `mode="send"` appears nowhere in this process. Read `kind` in the answer: `draft` means saved. A fresh draft can take a while to appear in a Gmail tab that is already open, so reload or search `in:drafts` before deciding it failed. Composing again only creates a duplicate.

## 5. A human reads every draft
A person reads each draft in their Drafts folder next to the numbers behind it: is the named behavior real and still true, does anything read as surveillance, and did someone already speak to this account this week. They send it from Gmail themselves, edit it, or delete it. Nothing goes to a paying customer before a person has read it.

## 6. Log each account for the week
- <tool:data_write> — one batch with `key` on `<account id>|<ISO week>`: the recent and baseline measures, which measure fell, the decline week, the seat count read in step 0, the outcome (steady, explained with its reason, already raised, owner call, open complaint, contraction, drafted, unmatched), the owner, and the draft's message id.
- The log is what the already-raised check in step 3 and the seat comparison in step 2 read next week, so write it for every matched account, including those that ended steady or explained, and for the unmatched ones: a quiet account and an account the run never saw must not look alike.

## Output
Accounts measured, and paying companies unmatched in analytics; accounts flagged, by measure; drops explained away, by reason; accounts handed to an owner because of a renewal inside the window or a seat contraction; accounts handed over because an unresolved ticket preceded the decline; drafts saved, with the mailbox they are in; and accounts skipped as already raised recently.