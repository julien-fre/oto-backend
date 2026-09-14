# Review customer accounts for upsell opportunities

**When to use it**: once a month, across every paying customer, when growth has to come from the accounts you already have and nobody can say which of them could run more volume through you. The CRM stage says one thing, real usage says another, and a note written at signing says a third. This measures what each account is actually billed for, reads what their calls say about the volume behind them, finds one dated signal, and leaves one sales-minded email draft per account for a person to edit and send.

```
              Scheduled routine, monthly
              "Which customers could run more volume through us? Prep this month's calls."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · List every active account                  │   attio_list, attio_entry
│  The customer list minus churned; a stage or a  │   attio_record
│  note is an old opinion, not usage.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Measure billed usage per period            │   data_rows
│  Matched on users' email domain, counted per    │   your usage export, no connector
│  subscription period, never per calendar month. │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ ghost record      a duplicate with no usage
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Rank on potential, not price               │   attio_note
│  What calls and notes say about the volume      │   attio_meeting
│  behind the account.                            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Find one dated signal                      │   theirstack_jobs_search
│  A named, dated signal from a connected tool,   │   apollo_bulk_enrich_organizations
│  or none said; never an invented hook.          │   serper_search
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Read every open thread before writing      ║   gmail_message
║  Last message, who owes the reply, and any      ║   attio_meeting
║  meeting already booked.                        ║   before the draft, never after
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ deferred          recent exchange or meeting booked
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Draft one email per account                │──▶  Gmail draft  saved, never sent
│  One point the reader did not have, aimed at    │   gmail_compose
│  more volume, never at a smaller plan.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · A human edits and sends                    │   the one human step
│  Each draft opened, edited, and sent only       │
│  when it is good.                               │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Write the call list                        │   data_write
│  Ranked calls, deferred accounts and why,       │
│  and what the run learned.                      │
└─────────────────────────────────────────────────┘

▪ terminal — the account stops there for this cycle
```

## The posture, before any step
Every email this produces exists to grow what the customer runs through you. Three situations, one reflex:
- **Above their plan**: they already pay overage at your highest unit rate. Offer the tier that lowers what each unit costs them.
- **Using without a plan**, or on a price set during a pilot and never revisited: revenue leaking. Put it under contract.
- **Well below their plan**: not oversized, a failed activation. There is volume still sitting outside, or a use case that went wrong once and was quietly dropped. Go and find it.

Never, in any email and whatever the account's state: offer to move them to a smaller plan, tell them they pay too much, put a number on the gap between what they pay and what they use, present a renewal date as a risk, or ask them to choose between using more and paying less. Unused volume is headroom already paid for. A stalled account is "getting back to the pace you had in **[month]**". An account that never started is "getting it running", never "you haven't got anything out of it".

## 1. List every active account
- <tool:attio_list> with `op="list"` to resolve your customer list's slug, **[your customer list]**. Never assume a slug from a previous workspace.
- <tool:attio_entry> with `op="query"` on that list, `limit=50`, advancing `offset` by the limit until a page comes back short. A full page is never the end.
- **Exclude only the churned stage.** Keep everything else, low-potential included. A stage and a free-text note record an opinion formed once and rarely revisited; neither predicts usage, and an account marked dormant can be sending work every day. Filtering on them is how the best expansion in the book gets skipped.
- <tool:attio_record> with `op="get"` on each entry's parent company: its domains (the key for step 2) and, if a website visitor tool syncs its fields onto the record, the latest visit data (a signal for step 4, at no extra call).

## 2. Measure billed usage per period
Usage comes from your product's own records, exported into a table on a schedule and read with <tool:data_rows>; no connector is involved. Four traps, each of which produces a wrong email:
- **The name.** Never identify an account by the company-name field inside your product. Sign-up flows store whatever the first user typed: a person's name, a test placeholder, a name with a random suffix. Match on the email domain of the account's users against the domains on the CRM record, with a filter like `{"<user email domain column>": {"in": [<the record's domains>]}}` and `fields` projected to what you need. A free-mail domain identifies nobody: for those users, match the individual addresses instead.
- **The duplicates.** Your product can hold an empty account under the company's real name next to the one actually in use. Finding a record with the right name proves nothing; the account with users and usage is the one. **An empty duplicate is the ghost-record exit**: flag it for cleanup and write nothing about it.
- **The subscription.** Read the plan from the subscription records themselves (plan, start date, end date, whether it is billed through your payment provider), never from summary columns copied onto the account record, which go stale and give false "no plan" readings. A demo or trial type on the account does not mean there is no paid subscription.
- **The usage.** Read the billed usage counter per subscription period, never a count of raw activity rows (uploads, events, calls): those double-count retries and miss what was billed, and on the same account the two can differ by a multiple. **Periods run from the subscription's anniversary date, not the 1st of the month.** Aggregating by calendar month invents spikes and collapses that never happened.

Place each account in one of the three situations above, from the current period against the plan and the previous periods for the trend.

## 3. Rank on potential, not price
The subscription amount is not the criterion: a small plan can be a test run by a company with enormous volume behind it.
- <tool:attio_note> with `op="list"`, scoped with `parent_object="companies"` and the record id. **Notes come back oldest first, ten at a time, with no sort and no date filter**, so the recent ones sit at the end: set `limit=50` and page `offset` until a page comes back short.
- <tool:attio_meeting> with `op="list"`, the one Attio object that windows by date: `ends_from` set to **[your lookback]**, paged by `pagination.next_cursor` until it is null. Keep meetings whose participants carry the account's domains, and open `op="transcript"` on a recording only when the notes say nothing about volume.
- What counts as evidence of potential: a named end customer, a tender, a number of sites or entities, a go-live date, a second use case raised by the person who signs. Rank accounts with that evidence first, whatever their plan.

## 4. Find one dated signal
Every draft needs at least one signal from a connected tool, dated and nameable, or the account is marked "no signal" and the email leans on usage alone. **Never an invented hook.**
- <tool:theirstack_jobs_search> for the account's open roles. Pass the domain as `company_domain_or` in `extra` rather than `company_names`, which is exact and case-sensitive; `posted_at_max_age_days` bounds recency and `limit` bounds the spend, billed per company returned. An open role for the work your product automates is a direct signal. <tool:theirstack_companies_search> reads the technologies detected in their postings, when a stack change is the story. **An empty result on a small company is normal**, not an error: don't retry, move on.
- <tool:apollo_bulk_enrich_organizations> with up to ten domains per call. It costs one credit per company either way: batching saves calls against the hourly enrich limit, not credits. Read headcount growth over six, twelve and twenty-four months and the per-department split: a growing team in the department that uses you is the expansion case in one line.
- <tool:serper_search> with `kind="news"` and `tbs="qdr:m"` on the company name. Set `country` and `language` to the account's market: the defaults are French, and a news query about an account elsewhere run with them comes back thin.
- The visit fields read in step 1: a recent visit to your pricing or product pages is dated and nameable.

## 5. Read every open thread before writing
For each account still in, establish four things before a word is drafted: the last message and its date, what is still unresolved, who owes the next reply, and any meeting already booked.
- <tool:gmail_message> with `op="search"` and a query like `from:<domain> OR to:<domain> newer_than:[lookback]d`, once per domain, then `op="get"` on the newest message of each thread to see who wrote last. **Run it on every mailbox that talks to customers** (the `account` parameter): a live conversation in a colleague's inbox is invisible from yours.
- <tool:attio_meeting> with `ends_from` set to now: anything upcoming with the account's people.
- Then one of three verdicts: write freely, write only about the open topic, or don't write. A technical exchange yesterday, a call three days ago, a meeting tomorrow: all three are "don't write", and the account leaves on the **deferred** exit with its reason recorded.

**This gate runs before the drafting, never after it.** A review pass over finished drafts is how a batch ends up writing to accounts in the middle of a live conversation: the drafts already exist, and a reviewer skims.

## 6. Draft one email per account
- <tool:gmail_compose> with no `mode`: the default saves a draft and nothing leaves the mailbox. Never pass `mode="send"`. Read `kind` in the response before reporting what happened. A fresh draft can be missing from an already-open Gmail tab for a while: tell the reviewer to reload or search `in:drafts`, not to look again.
- **The shape**: a greeting, a plain opening line, the open topic first if the gate found one, **one** substantive point the reader did not have before opening it, your customer booking link as a hyperlink on a few words of anchor text, and a closing formula on its own. Four short paragraphs at most. One link per email, never a raw URL, never proposed time slots.
- **No signature block.** The mailbox appends its own signature, and a name typed into the body gives the customer two.
- **What kills a draft**: paragraphs of analysis, a list of what you inferred about their organization, speculation about their internal life, a reproach for going quiet, a dump of usage statistics, and above all any sentence that invites them to shrink or leave.
- The customer's language and register.
- **Recipients**: one contact, plus **[the colleague who should see replies]** in copy. Keep that copy list in config and check it each run: a teammate who has left still sits in old threads, and copying them is the first thing a customer notices.

## 7. A human edits and sends
A person opens each draft, edits it, and sends the ones that are good. Not optional: this is outbound to paying customers.

## 8. Write the call list
- <tool:data_write>, one row per account: the situation (above plan, using without a plan, below plan), billed usage for the current and previous periods, the potential evidence and where it came from, the signal and its date or "none", the gate's verdict and reason, and whether a draft was created. Ranked by potential, so the list doubles as the month's call sheet.
- Deferred accounts carry their reason, so next month's run starts from them.
- What the run learned goes back into the process itself: a rule here that proved wrong gets corrected here, not in a side note nobody reopens.

## Output
Report: accounts reviewed, ghost records flagged, how many accounts sit in each situation, accounts deferred and why, drafts created, drafts carrying a dated signal against drafts leaning on usage alone, and the ranked call list.