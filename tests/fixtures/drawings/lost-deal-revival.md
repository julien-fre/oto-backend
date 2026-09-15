# Re-engage closed-lost deals with what changed since

**When to use it**: a deal that reached a demo and was lost is the warmest cold account you have, and a few months later the reason it was lost has often moved: a budget cycle turned over, the capability they needed shipped, the person who stalled it left. Re-reading every lost deal against the CRM, the public record, the people and your own tracker would take a rep days, so it never happens. Run this monthly: it rebuilds each deal's history, checks what changed, keeps only the deals where something answers the loss, and stages a paused campaign that a person launches.

```
              Scheduled routine, monthly
              "Find the deals we lost last winter that are worth reopening. Dry run first."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Fix the window, dry or live                ║   data_rows
║  A window with no rows in the table yet runs    ║   the first run on a window stages nothing
║  dry, whatever the request said.                ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Pull lost deals past the cooldown          │   hubspot_object
│  Lost between 90 days and 24 months ago, and    │   stage entry dates, not the current stage
│  once at a demo stage or later.                 │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ out of window     under 90 days or over 24 months
                         ├───────────────▶  ▪ never demoed      no entry date on a demo stage
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Read the history, classify the loss        ║   hubspot_object
║  Notes, emails and calls decide the reason;     ║   the reason field is only a hint
║  one opted-out contact stops the whole deal.    ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ do not contact    a contact on the deal opted out
                         ├───────────────▶  ▪ already open      the account has a live deal
                         ▼  a loss reason with its evidence
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  4 · Spot what has shipped since │  │  5 · Find what changed there     │   apify_run_sync
│  Linear work completed after the │  │  Funding, hiring and leadership, │   one batch run for every company
│  close, matched on substance.    │  │  each with a date and a source.  │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     ├──────────▶   ▪ company gone   acquired or shut down
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Confirm who is still there                 │   linkedin_unipile_profile
│  Each contact read against their own profile,   │   linkedin_unipile_search
│  and a replacement found in the same role.      │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · Keep deals with an answer, draft           ║   the table is the dry run's deliverable
║  A deal stays only when a change answers its    ║
║  loss; each keeper gets three drafted emails.   ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ nothing new       no change answers the loss reason
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Live run: stage a paused campaign          │   lemlist_enrich_bulk, lemlist_campaign
│  Work emails found, the campaign paused before  │   lemlist_create_lead, hubspot_object
│  any lead, and a note on each deal.             │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ unreachable       no usable work email at the account
                         ▼
┌─────────────────────────────────────────────────┐
│  9 · Post the recap; a human launches           │──▶  Slack recap  deal links and the paused campaign
│  Deals grouped by what answered the loss; the   │   slack_post_message
│  owner reads the drafts, then starts it.        │
└─────────────────────────────────────────────────┘

▪ terminal — the deal stops there and nothing further is spent on it
```

## Rule zero: nothing new to say means no email
These accounts already said no once. A second contact with no new information is how a sending domain's reputation gets spent, so a deal goes forward only when a change answers the reason it was lost, and every drop is counted rather than hidden. Free reads run before anything that spends credits, and nothing already skipped spends a credit afterwards. The run never sends: the campaign is created paused, the only CRM write is one note per staged deal, and no deal is reopened, moved or edited.

## 1. Fix the window, dry or live
- The window is either two dates from the request or the rolling monthly shape: the 30 days of closes that just crossed **[your cooldown, default 90 days]**. Consecutive rolling windows never overlap, so no account is picked up twice from the same loss. A first run can backfill any window between the cooldown and **[your ceiling, default 24 months]**.
- <tool:data_rows> — count the rows already in **[your lost-deals table]** for this window. **No rows means a dry run, whatever the request said**: steps 2 to 7 run and write only the table, and a person reads the eligible count and the drafts. A live run is allowed only over a window that already has its dry-run rows, which is how "dry run first" is enforced rather than remembered.
- A live run asks which sending workspace and sender to use, and never guesses. One run at a time per window: the signal and enrichment steps spend per company, and two overlapping runs pay twice.
- A run resumes from the table. A row already marked `drafted` is not read, scraped or enriched again.

## 2. Pull lost deals past the cooldown
- <tool:hubspot_object> — `op="search"` on `deals` with two filters: `dealstage` `IN` every lost stage id (a portal with several pipelines has several lost stages, passed as `values`), and `closedate` `BETWEEN` the window's bounds. Filters in one search combine with AND. 100 per page, paging with `after` until no cursor comes back. Ask for `dealname`, `closedate`, `closed_lost_reason`, `pipeline`, the deal owner, and the entry-date property of each demo-or-later stage (`hs_date_entered_<stage id>`; confirm the exact names with <tool:hubspot_property> `op="list"` on deals).
- **"Once reached a demo" is read from the stage entry dates, never from the current stage**, which is always lost. Keep a deal when any demo-or-later entry date is set; otherwise it leaves as **never demoed**.
- **Take the stage ids from your pipeline settings, not from the labels.** `dealstage` carries no options to read them from, and a portal that repurposed a default stage keeps the stage's original internal id, so an id that reads like "won" can be a mid-pipeline stage. Record the mapping once in the process's notes.
- Write every pulled deal to the table keyed on the deal id, the exits included with their `skip_reason`, so the count of deals considered is on record. A private app is capped at 190 requests per 10 seconds: page, don't burst.

## 3. Read the history, classify the loss
- <tool:hubspot_object> — `op="get"` on the deal with `associations=["notes","emails","calls","meetings","contacts","companies"]` returns the associated ids inline, which saves an `op="associations"` round trip per type. Then read each activity (`hs_note_body`, `hs_email_text`, `hs_call_body`, `hs_meeting_body`, each with `hs_timestamp`) in time order, and each contact (`firstname`, `lastname`, `email`, `jobtitle`, `hs_email_optout`). Confirm your LinkedIn URL property's internal name the same way.
- **Any contact with `hs_email_optout` true skips the whole deal** as **do not contact**, before any credit is spent. An account with an open deal right now leaves as **already open**: a win-back there collides with a live conversation.
- **`closed_lost_reason` is a hint, not the answer.** It is a dropdown picked at the end of a bad week, and "price" often turns out to mean no budget this year or a security review that never got through. Classify into one of four and write the evidence (the note or email line, with its date) on the row: **timing or budget**; **a missing capability**, named precisely enough to search for ("no audit trail", never "features"); **lost to a competitor**, named; **no decision**, went quiet.
- A deal lost to a competitor is kept, not skipped. Its renewal date is the recorded contract end, or `closedate` plus **[your typical contract term, default 12 months]**. A multi-year contract that is nowhere near its end is dropped at step 7 rather than written to now.

## 4. Spot what has shipped since
- <tool:linear_issue> — for each deal lost on a missing capability, `op="search"` with two or three phrasings of the capability, keeping only issues in a completed state that closed after the deal's `closedate`. **Match on substance, not wording**: a buyer who wanted "a way to see who changed what" and a shipped issue titled "audit log" are the same thing, and a literal match misses it.
- Search the company name as well, which surfaces the requests your team logged for that account while the deal was open. A request that has since shipped is the best line an email can carry.
- Keep the completion date. Something that shipped last month is a reason to write today; something that shipped a year ago raises the question of why nobody told them, and the email should say so plainly rather than present it as news.

## 5. Find what changed there
- <tool:apify_run_sync> — **one run for every company in the batch**, not a run per company, on a search actor (for example `apify/google-search-scraper`, whose `queries` input takes one query per line): `<company> funding`, `<company> acquisition`, `<company> hiring <the function you sell into>`, `<company> new <leadership role>`. Set `max_items` and `max_total_charge_usd` to **[your cost ceiling]** on every run.
- Every item kept carries a date and its source URL. A win-back that cites something the reader can check is a different message from one that gestures at momentum.
- Past 300 seconds the synchronous call answers 408: start with <tool:apify_run>, poll <tool:apify_run_status>, and read <tool:apify_dataset_items> only once the run has succeeded.
- A result showing the company was acquired or shut down flips the row to **company gone** here, and nothing further is spent on it.

## 6. Confirm who is still there
- <tool:linkedin_unipile_profile> — `op="person"` on each contact's profile slug (the slug, never the numeric member id). **Check the returned `public_identifier` matches the one requested** before trusting it. When the response lists `throttled_sections`, the experience section is empty because of a rate limit, not because the person has no job: retry that contact later in a separate pass, and keep concurrency low.
- <tool:linkedin_unipile_search> — for a contact who has no profile on record, search `advanced_keywords` with first name, last name and company, and accept a match only when the company corroborates it. For a replacement, search the lost contact's title at the company, and check the employer on every returned item, since a paginated page can silently drop the company filter. Space the calls: bursts earn a 429, and a 429 means slow down.
- Three outcomes. **Still there**: they get the email. **Gone, replacement found in the same role**: the replacement gets it, and the opener never references a conversation they were not part of. **Gone to a company already in your CRM** (<tool:hubspot_object> `op="search"` on `companies` by domain): flag it separately as a warm introduction there, which is often worth more than the win-back itself.

## 7. Keep deals with an answer, draft
A deal stays only when one of the other inputs answers its loss:
- lost on a missing capability, and that capability has shipped;
- lost on timing or budget, and there is a funding round, a new fiscal year or a new person in the buying role;
- lost to a competitor, and their renewal date falls within **[your renewal lead time, e.g. the next 3 months]**;
- went quiet, and the person who went quiet has been replaced.

Anything else leaves as **nothing new**, counted by loss reason. For each keeper, draft three emails:
- **Day 0**: what happened and roughly when, what changed, one question. Name the change plainly; if a capability shipped, say what it does rather than promising updates.
- **Day 5 and day 12**: two short, gentle follow-ups. **No breakup framing** ("closing your file", "last chance"): this account may be worth another try next year.
- No signature in any body, because the sender's signature is added by a person in the campaign. No customer name or figure that your team has not cleared for outside use.
- <tool:data_write> — the loss reason and evidence, what answered it, the contacts to address, the subject, `email_1`, `email_2`, `email_3`, and the stage `drafted`, keyed on the deal id. **A dry run ends here**, and the table is its deliverable.

## 8. Live run: stage a paused campaign
- <tool:lemlist_enrich_bulk> — one submit for every new or replacement contact, `actions=["find_email"]` with the LinkedIn URL, and `verify_email` for original contacts still in their seat. Collect every id in one <tool:lemlist_enrich_result> call, polling every 15 to 30 seconds; a result flagged `done` with nothing in it gets one more poll before you conclude nothing was found. Keep only deliverable work emails. A deal left with nobody reachable leaves as **unreachable**.
- <tool:lemlist_campaign> — `op="create"` lands the campaign **running**, so `op="pause"` is the very next call, before a single step or lead exists.
- <tool:lemlist_sequence> — `op="add_step"` three times: email steps with `delay` 0, 5 and 7 (days after the previous step, which puts the last one on day 12), each message a variable such as `{{emailBody1}}`, the first with `{{emailSubject}}`.
- <tool:lemlist_create_lead> — one lead per reachable contact, with every draft passed in `custom_variables` **at creation** (subject, the three bodies, the deal id) and `deduplicate=true`, which skips anyone already in another campaign. Setting variables after creation takes one call per variable. Then read `op="statutes"` back: level 3 blocks a launch.
- <tool:hubspot_object> — `op="add_note"` on each staged deal: the run, the window, the contacts enrolled, what answered the loss, and the campaign link. Mark the row `staged` with the campaign and lead ids.

## 9. Post the recap; a human launches
- <tool:slack_post_message> — one message to **[your sales channel]**: deals considered, dropped by exit with counts, kept deals grouped by what answered the loss (shipped, new signal, renewal, new contact), each with its deal link built on your portal's own host (records in a regional data center do not open on the default one), the warm introductions found, the paused campaign's link, and what the run spent. Past about 4,000 characters the text is split into threaded parts rather than truncated; read `split_into` instead of re-posting.
- The account owner reads the drafts in the campaign, adds the signature and starts it. The run never does.

## Output
Report: deals in the window, dropped by each exit (out of window, never demoed, do not contact, already open, company gone, nothing new, unreachable), kept deals by loss reason and by what answered it, contacts who had left and replacements found, warm introductions at accounts you already hold, capabilities matched to shipped work, drafts written, and on a live run the campaign's name, its paused state and the leads staged.