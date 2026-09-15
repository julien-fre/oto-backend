# Tell reps which target accounts are hiring, every morning

**When to use it**: your reps hold more claimed accounts than anyone can work in a morning, and the order they pick them in is arbitrary. A company whose own job ad says this month that your problem is live is the better call, and the sentence in the ad is the reason the rep opens with. This re-ranks the accounts you already own every morning, works through the whole register in rotation when it is larger than one morning's cap, and never adds an account.

```
              Scheduled routine, every weekday morning, or on demand
              "Run the timing sweep on our claimed accounts this morning."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Read the settings, check the key           ║   data_rows
║  Every setting comes from your config, and a    ║   oto_connector
║  missing one blocks the run.                    ║   a setting is never defaulted
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no setting        a config row is missing
                         ├───────────────▶  ▪ no key            TheirStack has no credential
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Take the domains from your register        │   data_rows
│  Claimed accounts, the longest unchecked        │   the register is read only
│  first, cut at the morning's domain cap.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ out of scope      named, but not in the register
                         ▼  the domains this run may spend on
┌─────────────────────────────────────────────────┐
│  2 · Read each company's own postings           │   theirstack_jobs_search
│  Page one query over the batch, then tell a     │   theirstack_companies_search
│  quiet company from an unknown one.             │   serper_search, then serper_scrape
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no posting        nothing published in the window
                         ├───────────────▶  ▪ stale             the posting predates the window
                         ├───────────────▶  ▪ truncated         the posting cap ended the walk
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Keep only what a sentence supports         ║   agency work and tool lists are dropped
║  A signal from your closed list with the exact  ║
║  sentence behind it, or nothing.                ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no quote          no sentence carries the claim
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write one row per observation              │   data_write
│  Keyed on domain, posting and kind, so a        │   every open row re-aged
│  re-read tomorrow is the same row.              │   answered domains stamped as checked
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Rank, and set apart who is in flight       │   hubspot_object, read only
│  Urgency, then freshness, then tier; open deals │   data_rows on your sequence table
│  and live sequences sit apart.                  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Post today's order to Slack                │   slack_post_message
│  Counts first, then a quoted reason per         │   data_write
│  company; the rest goes in a thread.            │   a quiet morning still posts its counts
└─────────────────────────────────────────────────┘

▪ terminal — it stops there and nothing further is spent on it
```

The exits are per domain, not per run, except `no setting` and `no key`: a normal morning drops domains at every one of them and still reaches step 6. Step 2 is a fallback chain, not a fan-out: one paged TheirStack jobs query for the whole batch, TheirStack companies only for the domains still absent after the last page, and a web search only for the ones TheirStack has never heard of. Nothing in this process writes to your CRM, your register or a sending tool, and nothing reaches a prospect.

## Rule zero: the ad times an account, it never qualifies it
Fit, stack and ownership are decided upstream, by whatever built your register. A job ad is good at exactly one thing: telling you the pain is live **now**. Every rail below follows from that.
- **It sources nobody.** A domain that is not already in the register is counted and reported as out of scope, never added, enriched or spent on. A rising out-of-scope count means someone is using the sweep as a list builder.
- **It never searches for the signal.** A job-board search for your problem's keywords returns the consultancies and agencies that sell that problem as a service, plus every company whose name collides with a keyword. The query is scoped by company, always.
- **No quote, no row.** An urgency claim with no sentence behind it cannot be checked by the rep who has to use it, so it is not written. A paraphrase is not a quote.
- **The denominator is reported every time**: domains checked, domains the job source could answer for, domains that produced a signal. Without the middle number, a quiet morning and a broken connector look the same. A domain the run never got to read is `truncated`, never counted as answered.

## Before the first run
- **A config table**, read by row name: **[freshness window in days]** (how old a posting may be and still count), **[recency band in days]** (inside it a signal keeps its full urgency), **[max domains per run]** (how many accounts one morning checks), **[max postings per run]** (the spend cap, counted in postings because the job search pages postings, not domains) and the report channel id. A missing row blocks the run; it is never replaced by a default.
- **A closed signal vocabulary**, one row per kind, with the phrases that match it in every language your prospects write ads in, and a base urgency. The kinds that tend to work, generically:

| Kind | What the ad says | Base urgency |
|---|---|---|
| owner hire | the role that would buy from you is being hired: **[the buyer's title in each language]** | now |
| problem language | the ad names your problem in its own words: **[your buyers' words for the pain]** | now |
| system migration | the system you plug into is being replaced or re-implemented | now |
| new leader | a new head of the function arrives with a mandate | soon |
| team growth | the team is scaling or structuring the process you touch | soon |
| capacity hire | **[roles that add volume without stating a pain]** | background |
| other | a sentence that plainly names the pain but fits no kind above | background |

Capacity is not pain, so it ranks low; a person hired to own the problem, or an ad that states it, ranks high. These bands are a hypothesis until your own replies say otherwise.
- **A signals table** keyed on `signal_key`, with `kind` and `urgency` declared as enum columns whose options are exactly the vocabulary above. Read the options back with `data_get_schema` before a run trusts the vocabulary: when the table enforces them, one invented value refuses the whole batch and loses the morning's observations. The opposite trap on a non-strict table: a misspelled field name is accepted into a column nothing reads, and nothing tells you.
- **A sweep-state table** keyed on `domain`, with `last_checked_on` and `last_answer`. It is the only thing that rotates the register: nothing in the sweep moves an account's last touch, and a domain with no posting is never raised, so without it the same oldest accounts would be re-read every morning and the rest never would.
- **Read access** to your account register and your outbound sequence table. The process reads both and writes neither.

## 0. Read the settings, check the key
- <tool:data_rows> — reads the config table by row name. A row that is absent takes the `no setting` exit and the run closes as blocked, naming the row. **The window is an answer, not a default**: a window stated in the request wins, and only in its absence does the config row apply. The recency band is a workspace decision and is never offered per run.
- <tool:oto_connector> — `op="list"`, `name="theirstack"` returns `ready`, or the exact step that is missing. **No key takes the `no key` exit and closes the run as blocked, in one line.** Do not fall back to web search for the whole register: it answers for a fraction of the domains at lower fidelity and makes the denominator meaningless, which is worse than not running.

## 1. Take the domains from your register
- <tool:data_rows> — reads the register. Keep the accounts a rep has claimed and is working. Drop the unclaimed ones (nobody to hand the order to), the ones already in a live conversation or deliberately paused, and the excluded ones. Carry the domain, company, owner, tier, last touch date and CRM company id forward, re-read every run because tier and last touch move.
- **A named list is intersected with the register, never unioned.** A named domain with no row takes the `out of scope` exit: counted, named in the message, and nothing spent on it.
- <tool:data_rows> — reads the sweep-state table for the surviving domains. **Order by `last_checked_on`, oldest first, with a domain never checked ahead of all of them, then by last touch, oldest first**, and cut at **[max domains per run]**. Count the domains cut, so a denominator smaller than the register is explained. Ordering by last touch alone would pin the sweep to the same oldest accounts every morning, because nothing here changes a last touch.

## 2. Read each company's own postings
- <tool:theirstack_jobs_search> — one call shape for the whole batch: `extra={"company_domain_or": [the batch], "include_total_results": true}`, `posted_at_max_age_days=[window]`, `limit` set explicitly, `page=0` and `full=true`. No title, technology or country filter: the domains are the whole query.
  - **`limit` counts postings, not domains.** It is results per page, and it bounds the spend either way. A company with a dozen open ads fills most of a page on its own, so the first page is not an answer for the batch: the domains pushed onto later pages would look empty, go to the companies search, come back known and be counted as `no posting`. That corrupts the middle number of the denominator and drops real signals.
  - **Walk the pages.** Increment `page` until a page returns fewer rows than `limit`, or the postings read reach `metadata.total_results`, or the next page would pass **[max postings per run]**. Only a domain still absent after the last page is empty.
  - **A walk cut by the posting cap answers nothing for the domains it did not reach.** Every domain not seen in the pages read takes the `truncated` exit: counted, never sent to the companies search, and left unstamped in the sweep state so it heads tomorrow's order. A domain truncated two mornings running means **[max postings per run]** is too tight for **[max domains per run]**.
  - **One query for the batch, never one per domain.** The postings returned are the same either way; a batched walk is a few round trips instead of one chance per domain to lose part of the run.
  - **`full=true` is not optional.** Without it each posting is projected to company, title, date, URL and location, with no description, and the sentence lives in the description. Every observation would then fail the quote rule, and the run would look like it worked while producing nothing.
  - **Read `metadata.truncated_companies` on every response.** Above zero, the credit balance cut the list, not the filter; report that batch as truncated rather than as complete.
  - **An empty `data` is a normal answer**, because coverage of small companies is partial. Do not retry.
- <tool:theirstack_companies_search> — only for the domains still absent after the last page: `extra={"company_domain_or": [the empty domains]}` and `limit` at least the number of empty domains, because at its default a longer list loses its tail and a dropped domain reads as unknown. A record back means the source knows the company and it simply published nothing in the window: the `no posting` exit, and a real answer. No record means unknown. That split is the middle number of the denominator.
- <tool:serper_search> — only for unknown domains, **scoped by the company, never by the signal**: `site_filter` set to the company's domain and a query of hiring words in the ad languages (careers, jobs, hiring, and their equivalents). Set `country` and `language` to the company's market, because the defaults are not neutral. A name-scoped query is the second resort, kept only when the page names the company and resolves to its domain. No posting URL back means `no posting`.
- <tool:serper_scrape> — on the posting URL the scoped search returned, never on an address built from the company name. A search snippet is cut mid-sentence, so the quote comes from the scraped text and is marked `source: serper`. A page that renders almost no text (a client-rendered job board) yields no quote and takes the `no quote` exit; the snippet is never promoted to a quote.
- A posting dated before the window takes the `stale` exit. If an earlier run already wrote it, step 4 ages it instead.

## 3. Keep only what a sentence supports
Each observation carries four things, or it is not an observation:
1. a `kind` from the closed vocabulary;
2. the **verbatim sentence** from the ad, copied exactly in the ad's language, with a translation in square brackets when it differs from your team's;
3. the posting URL, so the rep can open the ad the reason came from;
4. the posting date, which sets the recency adjustment.

At most one observation per posting per kind. Three filters run before a kind is assigned:
- **The department filter.** Many problem words carry a second meaning in another department: "onboarding" in a people-team ad is new hires, not new customers, and "retention" in an HR ad is staff, not accounts. Check which team the ad hires for before assigning the kind: the right word in the wrong department is dropped. Apply it strictly on the `now` kinds, where a false positive costs the most.
- **The third-party filter.** "For our clients", "on behalf of", "for the brands we serve": agencies and consultancies write your problem into their ads because it is their service line. Not a pain, not written.
- **The tool-list filter.** An ad listing tools as required skills ("experience with Tool A or Tool B") names two and identifies neither, and is read as evidence of nothing. A sentence saying a migration or re-implementation is under way is different: that is a `system migration` observation. It times the company and changes nothing the register says about what the company runs.

A sentence that plainly names the pain but fits no kind is written as `other` rather than forced into the nearest kind: a mis-filed kind hands the rep a wrong reason. `other` still needs its quote. An observation with no quotable sentence takes the `no quote` exit and is counted.

**Recency adjusts the band.** A posting inside **[recency band]** keeps its base urgency. One older than that but still inside the window drops one band: `now` becomes `soon`, `soon` becomes `background`. Past the window it is `expired` and never raised. Nothing rises above `now` and nothing drops below `background`.

## 4. Write one row per observation
- <tool:data_rows> — reads the signals table first, every `open` row and every row at today's domains, for what earlier runs wrote.
- <tool:data_write> — one batch with `key="signal_key"`, where `signal_key` = normalized domain + posting URL + kind. The same posting re-read tomorrow writes the same key and updates the same row instead of raising the company twice. A new posting at the same company is a new URL, so a new row, which is correct: it is new evidence.
- Fields: `company`, `domain`, `observed_on`, `kind`, `quote`, `quote_translated`, `job_url`, `job_posted_at`, `source` (`theirstack` or `serper`), `urgency`, `stage`, `raised_on`, `raised_reason`, `owner`.
- **`stage` is a lifecycle, written and never inferred**: `open` on creation; `raised` when step 6 names the company; `acted` when a person marks it, which this process reads and never sets; `expired` when the posting ages past the window. `raised` rows are left alone: a company raised and not acted on is a question for the rep, not for the sweep.
- **Ageing covers every `open` row, not only today's batch.** A row whose domain was not checked this morning still gets older, so the same batch recomputes `urgency` for every `open` row from its kind's base urgency, its `job_posted_at` and the recency band, and moves to `expired` any whose `job_posted_at` now falls outside the window, with no new call. Recomputing only the rows the job source re-returned would leave a stale `now` at the top of step 5.
- <tool:data_write> — one batch on the sweep-state table with `key="domain"`: `last_checked_on` set to today and `last_answer` set, for every domain the run answered for, whether it produced a signal, `no posting`, `stale` or `no quote`. A `truncated` domain, and one whose call failed, is left unstamped, so it comes first tomorrow instead of waiting a full rotation.

## 5. Rank, and set apart who is in flight
- <tool:data_rows> — ranks the `open` rows by `urgency`: declare the enum options in `now`, `soon`, `background` order, and `order_by="urgency"`, `order_dir="asc"` returns the most urgent first, followed through `next_cursor` until every open row is read. `order_by` takes one column, so the run applies the two tie-breaks itself on the rows it read: `job_posted_at`, newest first, then the register's tier, **only as a tie-break**. A top-tier account whose ads said nothing this month is not today's best call; a lower-tier one that posted the owner hire last week is.
- <tool:data_rows> — reads your outbound sequence table for every raised domain. A company already drafted or enrolled goes in a separate short block, because the action is different: not "call this company" but "this sequence can now open on a better reason, and here is the sentence".
- <tool:hubspot_object> — `op="get"`, `object_type="companies"`, the CRM company id from the register, `properties=["domain", "hs_num_open_deals", "notes_last_updated"]`. With no id on the row, `op="search"` with a filter on the normalized `domain`, never on the display name. An open deal, or activity in the last **[few days]**, means a conversation exists: the company joins the in-flight block. **Every CRM call here is a read**, and that includes never adding a note.

## 6. Post today's order to Slack
- <tool:slack_post_message> — to the report channel, in this order and nothing else: the counts (checked, answered, signalled, raised, cut by the domain cap, truncated by the posting cap, out of scope, and any batch the credit balance cut); the raised companies in rank order; the in-flight block; the out-of-scope names.
- **One line per company**: the company, its owner, the kind in plain words, the quoted sentence and the link to the ad. The quote is the reason and is never summarized. The message says where to act, which is the register row, not the message.
- **The top of the list goes in the message, the rest in a thread** under its `ts`, and the counts stay in the message. Long text is split into threaded parts, and the response carries `split_into` when that happened: read it before re-posting, because the Slack connector has no edit operation and a re-post on a wrong reading leaves a duplicate.
- **A morning with no signal still posts, with its counts.** Many checked, most answered, none signalled is a quiet morning; many checked and few answered is a broken connector. Without the middle number they are the same message.
- <tool:data_write> — on the signals table, keyed on `signal_key`: every row behind a company named in the message **or its thread** moves from `open` to `raised`, with `raised_on` set to today and `raised_reason` set to the exact line used, so tomorrow can tell a new raise from a repeat. The in-flight block counts as raised too, with `raised_reason` prefixed `in flight:`, so the same sequence is not re-flagged every morning and a rep can still see why it was set apart. Out-of-scope names have no signal row and write nothing.

## Output
One message in your channel with the morning's call order, a quoted reason per company, the in-flight block and the out-of-scope names. One row per observation in the signals table, each with its sentence, its posting URL and its stage, and a checked date per answered domain in the sweep state. The run reports the denominator (domains checked, answered and signalled), how many were raised, cut by the domain cap, truncated by the posting cap or out of scope, any batch the credit balance cut, and the connector or setting that failed if one did.