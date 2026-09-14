# Draft the quarterly LP letter

**When to use it**: gathering is where the quarterly letter's time goes and where its errors enter, from a six-week-old figure presented as a quarter-end number to a company that quietly stopped reporting. This run joins the mailbox and the holdings sheet into a dated Notion draft with the partners' work queue on top, and leaves what the quarter means to the partners.

```
              Scheduled routine, two weeks after quarter end
              "Draft this quarter's LP letter from the founder updates."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Check the quarter, then read holdings      │   sheets_spreadsheet
│  Stops if the quarter has a draft; otherwise    │   data_rows, notion_search
│  every holding and the quarter's transactions.  │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ draft exists      its link reported, nothing redone
                         ▼  no draft on record for this quarter
┌─────────────────────────────────────────────────┐
│  2 · Gather each company's dated figures        │   gmail_message
│  Founder updates matched on domain, each figure │   drive_file, for linked documents
│  kept with the date it describes.               │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Name companies that did not report         │   gmail_message
│  Each one listed with the date of its last      │   no date floor on this search
│  update, and no figures carried forward.        │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Reconcile against the fund's records       ║   data_rows
║  Founder figures against carrying values; each  ║   disagreements flagged, never resolved
║  value against last quarter plus transactions.  ║
╚════════════════════════╤════════════════════════╝
                         ▼  reconciled, or the difference stated
┌─────────────────────────────────────────────────┐
│  5 · Write the draft, exceptions on top         │   notion_create_page
│  A partner work queue first, then the letter    │   notion_append_blocks
│  with a source and a date on every figure.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Keep the quarter for the next one          │   data_write
│  Per-company rows and fund totals keyed on the  │
│  quarter, then the draft marked complete.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Partners clear the queue                   │   the one human step
│  Each exception resolved and the judgment       │
│  written before the letter goes out.            │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and says why
```

Steps 2 and 3 run once per holding. A company whose updates cannot be found or read is never dropped from the letter: it moves through every step with a status that says why, because a silent drop is how a struggling company disappears from what LPs are told.

## Before the first run
- **The holdings sheet is the roster.** One row per holding, with columns for company, **domains** (the company's own plus any alias it sends updates from), an optional **alias query** for updates sent through an investor-update platform, ownership, cost basis, carrying value, valuation date, currency, status (active, realized, written off) and, for realizations, proceeds and date. A separate tab holds **[your fund-level capital figures]** and the FX rates your valuations use, each with its date.
- **A transactions tab records every movement in carrying value**: date, company, type (investment, follow-on, revaluation, write-off, realization), amount and currency. The amount is the change the movement makes to the holding's carrying value: cost for an investment or follow-on, the signed change for a revaluation or write-off, and minus the carrying value removed for a realization (proceeds stay on the holding row). A carrying value edited in the holdings tab with no matching row here is exactly what step 4 exists to catch.
- Keep one config row the run reads with <tool:data_rows>: the spreadsheet id and tab names, the mailboxes to search, **[your LP letters parent page]** in Notion, the materiality thresholds below, and the snapshot table's name. Every snapshot row carries a `quarter` column and a `row_key` column holding `<quarter>|<company>`, `<quarter>|fund` or `<quarter>|letter`.

## 1. Check the quarter, then read holdings
- <tool:data_rows> before anything else, on the snapshot table's `<quarter>|letter` row for the quarter just ended. A page id with `complete=true`: this quarter already has a draft partners may be editing, so stop and report its link. A page id without the flag: an earlier run stopped partway, so stop and report it as an incomplete page; a partner archives the page and deletes the row, and the next run starts clean. Checking first means a rerun never repeats a quarter's mailbox searches and document exports.
- <tool:notion_search> on the title **LP letter · <quarter> · DRAFT** as a second check only, because Notion's search can take a while to index a page created minutes ago.
- <tool:sheets_spreadsheet> — `op="metadata"` for the tabs, then `op="read"` with **`formatted=false`** on the holdings, capital and transactions tabs. Formatted reads return display strings ("1.2M", "14%") that parse wrong; raw values come back as numbers, with percentages as fractions. Find columns by header, never by position, so a column someone inserted does not shift every value.
- The roster from this read drives steps 2 to 6. Non-reporters are found as the set difference between the roster and the companies with an update, never by noticing who is missing.

## 2. Gather each company's dated figures
- <tool:gmail_list_accounts> — once, to confirm every mailbox named in the config is connected; a missing one is named in the output, since its updates will look like non-reporting. Updates arrive in a shared inbox and in individual partners' inboxes, and forwarded copies arrive from whichever partner received them.
- <tool:gmail_message> — `op="search"` per holding and per mailbox (pass `account`), up to three queries, all with `after:` = the first day of the quarter and no `before:`, because quarter-end numbers arrive after quarter end:
  - `from:(<domain> OR <alias domain>)` for updates sent directly. **Match on the company's domain, never a founder's name**: founders change, and a name search finds every email that mentions them.
  - `"<domain>"` for forwarded updates, which quote the original sender's address in the body. This query also finds introductions and newsletters that mention the company, so keep only a message that reports the company's own figures.
  - Updates sent through an investor-update platform come from the platform's domain, so a domain match misses them: the holding's alias query in the sheet (`from:<platform domain> "<company name>"`) runs as a third search.
- Set `max_results` on every search rather than relying on the default of 20. A search that returns exactly `max_results` messages may have cut older ones off: repeat it with `before:` the oldest date returned and merge the two on message id.
- The same update reaching three partners is one update: dedupe on company, subject and sent date, keeping one message id.
- `op="get"` for each kept message. Figures in an attachment: `op="attachment"` returns small text inline and a PDF as a short-lived link. Figures behind a link to a Google Doc: <tool:drive_file> `op="export"`, which returns the document as markdown if it is shared with the connected account. A deck whose figures are only in chart images, or a link the account cannot open, is status **received, figures not readable**, which is a different entry from "did not report" and goes on the partner queue.
- For each update extract revenue, growth, runway in months, headcount and any financing event, **each with two dates**: the date the figure describes (the period the update names, "[month] numbers", "as of 30 [month]") and the date the email was sent. When the update names no period, the sent date is recorded as the as-of date and marked as such.
- **Keep the founder's own basis.** Revenue reported as MRR by one company and ARR by another stays as reported and labeled; growth month over month is never set beside growth year over year as if they were comparable. Converting a basis on the founder's behalf is an estimate.
- When a company sent several updates in the quarter, the one with the latest as-of date inside the quarter supplies each figure. If two updates give different values for the same period, both are kept and the disagreement goes on the queue.

## 3. Name companies that did not report
- For every holding with no update in step 2: <tool:gmail_message> `op="search"` with the same queries, **no date floor** and `max_results` set. Open the results newest first with `op="get"` until one reports the company's own figures, and take that message's sent date as the last reporting date. The newest hit is not necessarily an update, since the body query also matches introductions and newsletters. When a page of results holds no update, repeat with `before:` the oldest date returned. No update in any connected mailbox is recorded as "no update on record", not as a date.
- **Never fill the gap with last quarter's figures, and never drop the company.** Carrying a stale number forward is how a company that stopped reporting a year ago keeps appearing at a valuation nobody has revisited. Non-reporting is itself information, and LPs are entitled to it.

## 4. Reconcile against the fund's records
Cross-check every company's self-reported figures against what the fund records. **Where they disagree, flag it for a partner; never pick one and never average them.** A founder reporting growth against a carrying value that has not moved is either a stale valuation or an optimistic founder, and which one it is has consequences.

Per holding, the starting set of checks:
- a financing event reported in the quarter while the holding's valuation date predates it: round reported, holding not revalued;
- a priced round reported while ownership is unchanged: dilution not recorded;
- runway below **[six months]** while the carrying value is at or above cost;
- a shutdown, sale or down round reported while the holding is still marked active at its previous value;
- a figure whose as-of date is more than **[six weeks]** before quarter end: stated with its own date in the letter, and listed.

Fund level, from the holdings, capital and transactions tabs:
- total carrying value, unrealized gain (carrying value less cost, active holdings only), realizations in the quarter (proceeds, and the carrying value they removed from the transactions tab), and remaining capital as your capital tab defines it.
- **Roll forward before stating a total.** <tool:data_rows> with `filter` on last quarter's `quarter` reads the `<quarter>|<company>` rows and the `<quarter>|fund` row that step 6 of the previous run wrote.
  - **Per holding, in its own currency**: last quarter's carrying value plus the amounts of its transactions dated inside the quarter is the expected value, and it must equal the carrying value in the holdings tab. A holding with no row last quarter starts from zero. A value edited with no recorded revaluation, or a realization with no transaction, shows up here as an unexplained difference on that company, and goes on the queue.
  - **Fund level**: last quarter's total, plus the quarter's transactions converted at this quarter's recorded rates, plus the FX translation on holdings in another currency (last quarter's value at this quarter's rate less the same value at last quarter's rate), is the expected total. Compare it with the sum of this quarter's carrying values, within **[your rounding tolerance]**. When they differ, the letter shows the components and the unexplained difference instead of the total, and the difference goes on the queue.
  - On the first run with no snapshot, use the holdings sheet's previous-quarter carrying value column if it keeps one; otherwise state "not reconciled: no prior quarter on record". That run's snapshot is next quarter's starting point.
- Holdings in another currency are converted only at the rate and date recorded in the capital tab, never at a rate fetched during the run. With no recorded rate, report that currency separately.

## 5. Write the draft, exceptions on top
- <tool:notion_create_page> with `parent_type="page"`, `parent_id` = **[your LP letters parent page]** and the title **LP letter · <quarter> · DRAFT**. Write the new page id to the `<quarter>|letter` row immediately, before appending anything, with <tool:data_write> (`rows` holding that one row, `key="row_key"`). A run that stops mid-write then leaves a page id without `complete`, which the next run's step 1 reports instead of starting a second page.
- <tool:notion_append_blocks>, in order:
  1. **Before this goes out**: one `to_do` block per exception, grouped as companies that did not report, updates received but not readable, figures that disagree with fund records, figures dated more than [six weeks] before quarter end, and holdings or fund totals that did not roll forward. Each carries the numbers on both sides. This is the partner's work queue.
  2. **Fund-level position**, then **material changes this quarter**, then **company by company** with one heading per holding, then **realizations and outlook**.
- Rules for the letter:
  - Every figure carries its source and its dates: "(founder update, sent <date>, figures as of <date>)" or "(fund holdings, valued <date>)".
  - A company with no update appears with its last reporting date and no figures.
  - Discrepancies appear in the queue, not resolved in the text.
  - **No forward-looking claim about a company beyond what its founder stated**, and anything a founder did state is a quote attributed to them with its date.
- Notion takes at most 100 blocks per request, 2,000 characters per text object and about three requests a second. Append one section per call, split into chunks of at most 100 blocks sent in order, one call at a time: parallel appends land out of order. Split a long paragraph rather than letting the request fail.

## 6. Keep the quarter for the next one
- <tool:data_write> — one batch into **[your LP letter snapshot table]** with `key="row_key"`, every row carrying `quarter` and its `row_key`:
  - one `<quarter>|<company>` row per holding: each figure with its basis, as-of and sent dates, the reporting status (reported, received but not readable, not reported), the carrying value in the holding's currency with the FX rate used, the ownership used, and the flags raised;
  - one `<quarter>|fund` row with the fund totals, the roll-forward components and whether it reconciled;
  - the `<quarter>|letter` row with `complete=true`.
- The key makes a rerun in the same quarter update its rows instead of doubling them, and next quarter's step 4 reads the company and fund rows. `complete` goes on only in this batch, after the last append, so a draft marked complete always has next quarter's starting point written behind it.

## 7. Partners clear the queue
A partner works the to-do list before anyone reads the letter: chases the non-reporters, corrects the holdings sheet or records the missing transaction, notes why a founder's figure stands, and explains any unreconciled difference. Then the partners write the judgment on what the quarter means, which is the part LPs are actually paying for, and send the letter themselves. The run never sends anything to an LP.

## Output
Holdings on the roster; companies with an update found, with the as-of date used for each; companies whose update was received but not readable; companies that did not report, with their last reporting date; figures dated more than [six weeks] before quarter end; every discrepancy against fund records, with both values; holdings whose carrying value did not roll forward, and whether the fund totals reconciled or the unexplained difference; and the Notion draft's link. A run that stopped at step 1 reports only the existing draft's link and whether it was complete.