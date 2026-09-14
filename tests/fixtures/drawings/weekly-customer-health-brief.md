# Post a weekly customer health report to Slack

**When to use it**: you have more paying accounts than anyone can check dashboards for every week, and an account that goes quiet in the product is noticed at renewal, too late. Once a week this joins the customer pipeline in HubSpot, product usage in PostHog and feedback threads in Productlane, classifies churn risk against criteria your team wrote down, writes one brief to Notion and posts a short ranking of where to look first. It never contacts a customer.

```
              Scheduled routine, weekly
              "Run this week's customer health brief."
                         │
                         ▼
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  1 · Pull usage for paid accounts│  │  2 · Read the customer pipeline  │   hubspot_object
│  Your health metrics per account │  │  Every open deal with company,   │   hubspot_owners
│  over four weeks, from PostHog.  │  │  owner, stage and revenue.       │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     ├──────────▶   ▪ zero deals   wrong pipeline id, nothing posted
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Match each account to its usage            │   tier recorded on every row
│  Domain first, then three tiers of name match;  │
│  an unmatched account stays listed.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Add engagement and support                 │   hubspot_object
│  Recent calls and notes in the CRM, feedback    │   productlane_threads  read only
│  threads and their pain level.                  │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Classify the churn risk                    ║   missing data is not a signal
║  Count strong and weak signals against the      ║
║  written criteria: High, Medium or Low.         ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Write the brief                            │──▶  Notion page  one per week, callout colored by risk
│  An executive summary, then one section per     │──▶  health table  one row per account per week
│  account sorted by monthly revenue.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Post the ranking to Slack                  │   slack_post_message
│  At most five accounts under a hard cap,        │
│  each account's detail in the thread.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · A person reaches out                       │   the one human step
│  The account owner reads the brief and acts;    │
│  the run never contacts a customer.             │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and posts nothing
```

Steps 1 and 2 are independent reads; step 3 needs both. Steps 3 to 5 run once per account. An account the matching cannot place still gets a section and a row, marked unmatched, because a silent drop is how an account disappears from the brief. If classification fails on one account, its section keeps the raw metrics.

Before steps 1 and 2, the run reads one config row with <tool:data_rows>: the customer pipeline id, its open stage ids with their labels, the internal names of your monthly revenue property and of the analytics property that marks a paying plan, the Notion parent page and the Slack channel.

## 1. Pull usage for paid accounts
- <tool:posthog_schema> first, for the real event and property names, including the one that marks a paying plan. A property name from memory returns a confident zero.
- <tool:posthog_group> `op="types"` for the index of your company group. An empty answer means the project has no group analytics: group people by email domain instead, and say so at the top of the brief.
- <tool:posthog_query> with `hogql`, **one query per metric, grouped by account across every paying account**, never one query per account, which turns accounts times metrics into that many calls. Each query filters on **[your paid-plan property]**, groups on the account key, covers the last four weeks and buckets by week, most recent first.
- Pick **[your health metrics]** from what the product is for. A workable set: weekly active users (`uniq(person_id)`, never a count of distinct ids, which counts devices), event volume this week against last, the four-week trend of both, **[your key feature]** adoption, and per-user week-over-week change, which catches a few users collapsing while the account total holds. Keep the raw values; step 6 prints them.
- An unbounded query stops at 101 rows and sets `hasMore`. Aggregate in the query and size your own `LIMIT` to the rows the query really returns, not to the account count: above **[paying accounts] × 4** for a weekly series (one row per account per week), above **[paying users] × 2** for the per-user comparison (one row per user per week). If `hasMore` still comes back true, treat that query as failed and rerun it with a higher `LIMIT`; a truncated result makes every account past the cut silently read as "no data".
- A 429 is retried after its `Retry-After`, on that one query. Never carry on with a metric missing for half the book.

## 2. Read the customer pipeline
- <tool:hubspot_object> `op="search"`, `object_type="deals"`, with `filters` on `pipeline` EQ **[your customer pipeline id]** and `dealstage` IN **[your open stage ids]**, `properties` = deal name, `dealstage`, the owner id (hubspot_owner_id) and **[your monthly revenue property]**, `limit=100`. Then pass the previous page's `paging.next.after` as `after` until no cursor comes back. One page is not the pipeline.
- `op="get"` on each deal with `associations=["companies"]` returns the company id inline, which saves an associations call per deal. Then `op="get"` on each company for its name, domain and the activity dates step 4 uses.
- <tool:hubspot_owners> once per run, into an owner id to name map, never once per deal.
- Stage labels come from the config row, not from HubSpot: <tool:hubspot_property> returns `dealstage` with an empty options list, because stages belong to a pipeline and the connector does not read pipelines. A brief that prints raw stage ids is the symptom.
- **Zero deals is almost always a wrong pipeline id**, not an empty book. Stop, post nothing, and report it. A quiet brief built on an empty read is the worst output this process can produce.

## 3. Match each account to its usage
- Domain first, when your analytics groups carry one: equality on the normalized domain (lowercase, no `www.`).
- Then on name, in this order: exact match, case-insensitive; one name contained in the other; more than **[half]** of the name tokens in common. Strip legal suffixes before comparing.
- Record the tier that matched on the row: `domain`, `exact`, `contained`, `token` or `none`. Containment on a short name is the tier most likely to pair two unrelated companies, and the tier column is how a person spots it in the table.
- **An unmatched account is written down, never dropped.** Its section reads "no usage data", its row says `matched = none`, and the executive summary names every one.

## 4. Add engagement and support
- <tool:hubspot_object> `op="associations"` for every account, from the company (`object_type="companies"`) to `to_object_type="meetings"` and to `"notes"`, then `op="get"` on those ids with `properties` = the timestamp and body (`hs_timestamp`, `hs_meeting_body`, `hs_note_body`), keeping what falls inside **[60 days]**. Call summaries logged on the company are the richest engagement signal: they say what the customer said, not only that someone spoke to them. The latest one is quoted in the account's section.
- The company's last contact and last activity dates, read in step 2 (internal names `notes_last_contacted` and `notes_last_updated`; confirm them once with <tool:hubspot_property> `op="list"` on companies), date the silence for an account with nothing logged in the window.
- <tool:productlane_companies> `op="search"` with `domain`, only the first week an account appears (no mapping row yet): the Productlane company id goes into a small mapping table keyed on the HubSpot company id, so it is not resolved again.
- <tool:productlane_threads> `op="search"` with `company_id`, `created_after` = **[60 days]** ago and `limit=200`, following `cursor` until none comes back. The default page is 50, and one page is not an account's history. Keep the thread count, the open count, the highest `pain_level` in the window and the date of the latest thread.
- **A HIGH pain level on an open thread is a signal in its own right** and outranks a raw count. Three trivial questions and one unresolved blocker are not the same account.
- **`send` and `comment` are one operation name apart in the same tool.** `op="send"` messages the customer through whatever channel the thread came from; `op="comment"` is an internal note. This process calls neither. A health brief that messages an at-risk account by accident is the worst failure it has available.
- If Productlane does not answer, support reads "no data" and the brief lists the accounts that affected.

## 5. Classify the churn risk
The risk criteria live on a page your team owns, read each run with <tool:oto_doc> `op="get"`. The run counts signals against that page, in its wording; it never re-invents them. The set below is only an example, to replace with your own page:

**Strong signals**
- a single-week drop above **[40%]** in events or weekly active users;
- either one declining in **[3]** of the last **[4]** weeks, ignoring moves under **[15%]**;
- several individual users each down more than **[50%]**;
- **[your key feature]** adoption below **[your floor]**;
- very low activity on an account whose own history was higher.

**Weak signals, context only**: partial **[your key feature]** adoption, **[your setup milestones]** untouched in **[30 days]**, a moderate per-user decline.

| Risk | Example criteria, replace with your page's |
| --- | --- |
| High | **[3 or more]** strong signals, or a sustained multi-week decline above **[40%]** |
| Medium | **[1 or 2]** strong signals, or **[1 strong plus 2 weak]** |
| Low | No strong signals |

- Read trends as a person would: a recovery after a decline is positive; a few users declining while the heaviest users hold is not account-wide; an absolute number means nothing without the account's own history.
- **Missing data is not a signal.** "No data" cannot be assessed, and an account with every source empty is Low with the note "insufficient data", never High by default.
- Engagement and support adjust the reading: no contact in **[60 days]** alongside declining usage is a strong negative; active support usage is positive, because the account is using the product; several unresolved high-pain threads are a risk.
- Per account: the level; the signals as lines that carry their numbers ("events down from [x] to [y] over four weeks"); a summary of three to five sentences; two or three recommended actions. Growing usage with warm engagement gets an expansion note, not a blank.

## 6. Write the brief
- <tool:notion_create_page> with `parent_type="page"`, `parent_id` = **[your brief parent page]** and a title carrying the ISO week, so each week's page is findable by name.
- <tool:notion_append_blocks> in order. The executive summary first: accounts in the pipeline, monthly revenue under management, the High, Medium and Low counts, and the unmatched accounts by name. Then one section per account, sorted by monthly revenue, highest first: name and revenue as the heading, stage and owner, the metrics with their four-week trend, a callout colored red for High, orange for Medium, green for Low, then signals, summary and actions. Append one account section per call, one call at a time: Notion takes at most 100 blocks a request and about three requests a second, and parallel appends land out of order.
- <tool:data_write> as one batch: `rows=[...]`, each row carrying `row_key: "<ISO week>:<company id>"`, with `key="row_key"` (or the table's declared `schema.key`), so a rerun in the same week merges onto its rows instead of doubling them. Columns: week, company, domain, deal id, owner, stage, revenue, matched tier, each metric, strong and weak signal counts, risk, signals, summary, actions, engagement note, support note, Notion link, Slack `ts`.

## 7. Post the ranking to Slack
- <tool:slack_post_message> to **[your customer team's channel]**, **under a hard cap of 1,100 characters**: a header line, at most five ranked accounts, one line on the portfolio, the link. Rank by risk level, then by revenue within a level; Low accounts appear only when fewer than five are High or Medium.

```text
*Customer health · <ISO week>* · <n> accounts · <n> High · <n> Medium · <n> Low
1. *<Account>* (<revenue>/mo) · High · <the deciding signal, with its numbers>
2. *<Account>* (<revenue>/mo) · Medium · <same shape>
<one line: unmatched accounts, or a recovery worth knowing>
Full brief: <Notion link>
```

- One sentence per account, under 200 characters, carrying the numbers that decided the level.
- Then one thread reply per ranked account on the post's `ts`: signals, summary and actions, at most 1,200 characters each. Write the `ts` onto every row.
- **Post every week, quiet portfolio or not.** A missing post is how the team learns the run stopped. The run's notes about itself, retries and slow calls included, never reach the channel.

## 8. A person reaches out
The account owner reads the sections for their accounts, reaches out where the brief recommends it, and logs the outcome in the CRM. When an owner disagrees with a level, the reason goes onto the criteria page, where the next run will read it.

## Output
Deals read; accounts matched, by tier, and unmatched, by name; the High, Medium and Low counts; accounts whose support or usage data was missing; the Notion page link and the Slack post `ts`.