# Weekly BDR pipeline report from HubSpot

**When to use it**: your SDRs book meetings for your AEs, and a week of meetings and deals scattered across CRM views tells nobody whether the SDRs fed the AEs. Every Friday evening this reads the week in HubSpot, counts per rep the meetings booked, the discovery meetings, the deals created, the stage entries and the wins, keeps one row per rep per week and a team row with an SDR-by-AE crosstab, refreshes a Google Sheet, and posts three lines to your sales channel. It says nothing when nothing moved.

```
              Scheduled routine, every Friday evening, or on demand
              "Run the BDR pipeline report for this week."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Fix the week and the reps                  ║   hubspot_owners
║  The ISO week as timestamps, last week          ║   data_rows on the roster and config
║  closed out, owners mapped by email.            ║   never matched on a first name
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ already posted    this week's summary went out
                         ▼  a week not yet posted
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  1 · The week's meetings         │  │  2 · The week's deals            │   hubspot_object
│  Booked this week, with type,    │  │  Created, stage entries, wins,   │   one search per stage, paged
│  outcome, owner, linked deals.   │  │  each amount in its currency.    │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼  both reads complete
┌─────────────────────────────────────────────────┐
│  3 · Count per rep and across                   │   hubspot_object, owners of linked records
│  Booked, discovery, untyped, deals by stage,    │   untyped is never counted as discovery
│  won per currency, and SDRs by AEs.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write the rows first                       │   data_write
│  One row per rep per week and one team row,     │   keyed on week and owner id
│  so a failed sheet or post loses nothing.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Refresh the Google Sheet                   │   sheets_spreadsheet
│  Rebuild the history and dashboard tabs from    │
│  the rows, when a sheet is configured.          │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing moved     rows kept, nothing posted
                         ▼  at least one meeting, deal or win
┌─────────────────────────────────────────────────┐
│  6 · Post the summary to Slack                  │   slack_post_message
│  Three lines in the channel, one per rep in     │
│  the thread, discovery shown as a floor.        │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and posts nothing
```

Steps 1 and 2 run in parallel because neither read needs the other; the counts in step 3 need both. The sheet is optional and skipped with a note when none is configured. Every HubSpot call is a read: this counts a week, it does not update a deal, classify a lost one or message anyone outside the sales channel.

## Rule zero: definitions are written down, never inferred in a run
- **A discovery meeting is a meeting whose type is [your discovery meeting type].** A meeting with no type is **untyped**, never discovery, and nothing infers a type from a title. When reps skip the type field, discovery is a lower bound, and every output says so next to the untyped count.
- **Reps are matched by owner id and email, never by first name.** Two reps can share one, and an owner record can carry a nickname.
- **Won means `hs_is_closed_won` is true**, never a stage id. Internal stage ids are fixed when a stage is created and survive every rename, so an id that reads as won can label a mid-funnel stage in your portal.
- **Two currencies are never added.** A won total is per currency.
- **Every count is by owner, and every owner is read.** A deal or meeting linked to this week's activity but created in an earlier week still has its owner read, never guessed from the rep who booked the meeting.
- A definition changes in the one place it is written, not in a run.

## Before the first run
- **A roster**: each rep's email and role (SDR or AE). An owner absent from it counts as `other`.
- **A config row**: the Slack channel id, the pipeline id you report on and its stage ids in order, the optional Google Sheet id, and the names of its history and dashboard tabs. **Copy the stage ids once from the pipeline settings in HubSpot**, because `hubspot_property` returns `dealstage` with an empty options list: stages belong to a pipeline, not to the property.
- **The sheet, if you want one**: create the history and dashboard tabs by hand before the first run, and give edit access to the Google account the connector acts as. The run writes and clears ranges on tabs that already exist. Keep both tabs for the report alone, because the run rebuilds them.
- **A weeks table** keyed on `row_key`, which step 4 fills.

## 0. Fix the week and the reps
- **The window** is the ISO week of the run: Monday 00:00 in **[your reporting time zone]** to the moment the run starts. On demand, a named ISO week runs Monday to Sunday. Both bounds become epoch milliseconds, because HubSpot search filters take dates that way, and the bounds go into every row as `week_start` and `week_end`.
- <tool:hubspot_owners> — owner id, email and name for every owner. Match each to the roster on email; a request naming specific reps is matched on email or owner id too.
- <tool:data_rows> — reads the config row, then the weeks table for `row_key = "<YYYY-WW>|team"` of this week and of last week.
- **Close out last week first.** A Friday-evening run stops before the weekend, so a meeting booked or a deal created on Saturday or Sunday would enter no week at all. When last week's team row stores a `week_end` earlier than its Sunday 24:00, run steps 1 to 4 again for last week's full Monday-to-Sunday window, update the same rows with `week_end` set to Sunday 24:00, and post nothing for it. Step 5 then rebuilds the sheet with the full week. A week already closed out is not read again.
- **A team row for this week that already carries a `posted_ts` means this week was posted**: take the `already posted` exit and re-read nothing. Only a request that explicitly says to post again skips this guard, and it then updates the same rows rather than adding a second set.

## 1. The week's meetings
- <tool:hubspot_object> — `op="search"`, `object_type="meetings"`, filtered on `hs_createdate` between the window bounds, `properties=["hs_createdate", "hs_timestamp", "hs_activity_type", "hs_meeting_outcome", "hs_meeting_title", "hubspot_owner_id"]`, `limit=100`, following `after` until it is absent.
- **Booked means `hs_createdate`, not `hs_timestamp`.** `hs_timestamp` is when the meeting takes place; filtering on it reports next week's calendar as this week's bookings.
- A single search stops at 10,000 results. If a week could exceed that, split the window by day rather than trusting a result that ends exactly there.
- <tool:hubspot_object> — `op="get"` per meeting with `associations=["deals", "contacts"]` returns the linked ids inline: one call per meeting instead of one associations call per meeting per type. It returns ids only, not the linked deals' owners, which step 3 reads.
- Keep per meeting: id, owner, created, takes-place time, type (empty stays empty), outcome, the linked deal and contact ids.

## 2. The week's deals
- <tool:hubspot_object> — `op="search"`, `object_type="deals"`, three kinds of search, each filtered on `pipeline` equal to your pipeline id so another pipeline's lookalike stage never counts, each with `properties=["dealname", "dealstage", "pipeline", "amount", "deal_currency_code", "createdate", "closedate", "hs_is_closed_won", "hubspot_owner_id"]` and paged by `after`:
  - **Created**: `createdate` between the window bounds.
  - **Stage entries, one search per stage id**: the `hs_v2_date_entered_<stage id>` property between the bounds. A deal that crossed two stages this week counts in both, which is what a funnel should show.
  - **Won**: `hs_is_closed_won` equal to true and `closedate` between the bounds.
- **Amounts are strings**, each in its `deal_currency_code`. Parse them, keep the currency with the number, and sum per currency only.
- <tool:hubspot_object> — `op="get"` with `associations=["meetings"]`, for the deals created this week only, returns the ids of every meeting linked to each. **A deal is SDR-sourced when at least one linked meeting, from any week, is owned by a rep whose role is SDR.** A meeting booked this week already has its owner from step 1; one booked earlier gets it in step 3. This is a default definition: state it on the sheet, and change it where your definitions live.

## 3. Count per rep and across
Two reads fill in the owners the week's searches did not return; everything after them is arithmetic.
- <tool:hubspot_object> — `op="search"`, `object_type="deals"`, one filter `{"propertyName": "hs_object_id", "operator": "IN", "values": [up to 100 ids]}` over the deal ids linked to this week's meetings that step 2 did not already return, `properties=["hubspot_owner_id", "pipeline"]`, one call per 100 ids. Those deals are often older than the week, so none of step 2's searches holds them, and without their owner a crosstab cell cannot be filled. **A linked deal in another pipeline lands in no AE column**: the meeting counts in `no deal` unless it also links a deal in your pipeline.
- <tool:hubspot_object> — the same `IN` search on `object_type="meetings"` over the meeting ids linked to this week's created deals that step 1 did not return, `properties=["hubspot_owner_id"]`. That owner is what the SDR-sourced test reads for a meeting booked in an earlier week.
- **Per rep**, by owner id: meetings booked, discovery, untyped, a count per outcome value (an empty outcome counted as its own value), deals created, SDR-sourced deals, entries per stage, won count, and won amount per currency. A meeting counts for its owner, a deal for its owner.
- **The crosstab**: SDRs as rows, AEs as columns, each cell the meetings an SDR booked this week whose linked deal in your pipeline an AE owns, plus a `no deal` column. A meeting linked to deals of two AEs lands in both cells, so team totals come from the meeting count, never from summing the crosstab.
- <tool:hubspot_object> — **cohorts, optional**: if your contacts carry a multi-select property for **[your cohort or segment]**, read it in one `op="search"` on `contacts` with `hs_object_id` `IN` the week's contact ids (100 per page), with `properties=["associatedcompanyid", "[your cohort property]"]`, instead of one read per contact. Split each value on semicolons, which is how HubSpot stores a multi-select, and count per cohort the meetings and discovery meetings, a company counted once per cohort through `associatedcompanyid`.
- **The team row** is the sum of every count, plus the crosstab and the cohorts, and `moved` set when any meeting, created deal, stage entry or win exists.

## 4. Write the rows first
- <tool:data_write> — one batch with `key="row_key"`: `"<YYYY-WW>|<owner id>"` per rep and `"<YYYY-WW>|team"` for the team. A re-run of the same week, including last week's close-out, updates the same rows instead of adding a second set.
- Rep rows: week, `week_start`, `week_end`, owner id, rep name, email, role and every count from step 3. Team row: the sums, the crosstab and cohorts as JSON, `moved`, `sheet_url`, `posted_ts` (empty until step 6), and notes such as "no sheet configured".
- **Rows before the sheet and the post**, so a refused sheet or a failed post loses nothing and the next run can finish the job.

## 5. Refresh the Google Sheet
Only when a sheet id is configured.
- <tool:sheets_spreadsheet> — `op="metadata"` first: it confirms both tabs exist and the connected account can reach the sheet. A write to a missing tab fails, and a 403 means the sheet was never shared with that account.
- <tool:data_rows> — reads every rep and team row in the weeks table. **The history tab is rebuilt from the table, never appended to.**
- <tool:sheets_spreadsheet> — `op="clear"` on the history range, then one `op="write"` from `A1`: a header, then per week one row per rep and a subtotal row. Rebuilding means a re-run never doubles a week, a closed-out week replaces its Friday figures, and a corrected definition restates every past week the same way.
- <tool:sheets_spreadsheet> — `op="clear"` on the dashboard range, because the crosstab's size changes with the reps and a smaller grid would leave last week's cells behind, then one `op="write"`: a title, `Last updated <ISO timestamp>` in row 2, the team row and the crosstab.
- Each call writes one range. Formatting is best effort and never blocks the write. Store the sheet URL on the team row.
- **`moved` is false**: the rows are written and the sheet stamped, then the run takes the `nothing moved` exit and posts nothing. A silent Friday means a silent week, and the rows say which.

## 6. Post the summary to Slack
- <tool:slack_post_message> — to the configured channel id.
- <tool:slack_join_channel> — only when the post answers `not_in_channel`, and only for a public channel; a private channel needs a person to invite the app, and the run says so instead of posting elsewhere.

```text
*BDR pipeline · week <WW> · <start> to <end>*
Meetings booked: <n> · <d> discovery (a lower bound: <u> untyped) · <c> completed, <s> no-show
Deals: <n> created (<b> SDR-sourced) · entered <stage>: <n>, <stage>: <n> · won <n> for <amount per currency>
Per rep in the thread · Sheet: <link>
```

- Then, under the message's `ts`, one line per rep with a non-zero count, `<rep> (<role>) · <n> booked (<d> discovery, <u> untyped) · <n> deals created · won <amount per currency>`, and one line naming the largest crosstab cell.
- **Write `posted_ts` on the team row immediately after the post.** That is the replay guard step 0 reads: a scheduled run and an on-demand run in the same week then post once.

## Output
Three lines in your sales channel with the per-rep lines in the thread, or no post on a week where nothing moved. One row per rep per week and one team row with the crosstab and cohorts in your weeks table, last week's rows restated with its weekend, and the history and dashboard tabs refreshed when a sheet is configured. The run reports the week covered, whether last week was closed out, whether it posted and why not, the untyped share behind the discovery floor, and any sheet or channel access that failed.