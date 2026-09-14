# Find companies that use a competitor's product

**When to use it**: you compete with **[competitor]**, and its own marketing names the companies that bought it. A company that has publicly said it uses **[competitor]** has told you three things at once: it has the problem, it has budget for it, and it has already been through a procurement cycle for it. That is a better starting point than any firmographic filter. It is also the easiest motion to get wrong, because these accounts have already solved the problem, and this process is built around that.

```
              Scheduled routine, every Monday morning
              "Run the radar on our main competitor and tell me which accounts are new since last week."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the sources before spending          ║   apify_actor, dropcontact_credits
║  Free reads confirm the watch list, scraper     ║   lemlist_campaign reports on the template
║  token, credits and campaigns that cannot send. ║   data_rows on the accounts table
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read the competitor's customer pages       │   apify_run, apify_run_status
│  Crawl its case studies by following links,     │   apify_dataset_items
│  then retry pages that came back empty.         │   never a synchronous crawl
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Corroborate, never discover                │   theirstack_jobs_search
│  Job ads, post comments and review sites add    │   serper_search on review sites
│  evidence to accounts already found.            │   apify post scraper, capped
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ partner           a reseller or integration partner
                         ├───────────────▶  ▪ unsourced         no URL a human could open
                         ▼  a user, with a source URL
┌─────────────────────────────────────────────────┐
│  3 · Resolve the firmographics                  │   linkedin_aiark_search, companies
│  Revenue band, staff and a company id for each  │   theirstack_companies_search, limit 5
│  domain, vetted on headcount.                   │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ entity unclear    the record is another legal entity
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Score against your account band            ║   no provider is called here
║  Revenue, headcount, country, sector and        ║   the bar is a real bar
║  evidence strength, from the row alone.         ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ out of band       below the bar or disqualified
                         ▼  scored above the bar only
┌─────────────────────────────────────────────────┐
│  5 · Find and enrich two or three buyers        │   linkedin_aiark_search, people
│  People on the vetted record, ranked on title,  │   dropcontact_enrich, dropcontact_result
│  then one work-email batch for those kept.      │   lemlist_enrich for the top band only
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no contact        nobody current in a buyer role
                         ├───────────────▶  ▪ unreachable       no work email found
                         ▼  a deliverable work address
┌─────────────────────────────────────────────────┐
│  6 · Write the opener for review                │   data_write, one row per person
│  Ground each opener in that account's own       │   nothing reaches Lemlist yet
│  evidence, never naming the competitor.         │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · A human approves, name by name             ║   data_app on the rows to review
║  A person reads each opener, edits it if        ║   nothing is staged before this
║  needed, and approves or rejects it.            ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ rejected          a person said no, with a reason
                         ▼  approved rows only
┌─────────────────────────────────────────────────┐
│  8 · Stage the approved leads                   │   lemlist_campaign, duplicate the template
│  Only approved people, with the approved        │   lemlist_create_lead, opener included
│  opener, into a paused campaign.                │   never a launch call
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

## Three rules that pay for the whole thing
- **Detection comes from one place, and everything else only corroborates.** The competitor's own customer pages create accounts; job ads, post engagement and review sites add evidence to an account that already exists and never add one of their own. Reverse that and the radar fills with noise.
- **Enrichment runs after scoring, never before.** Detection returns a short list, and resolving and scoring it costs almost nothing. Only what survives gets a paid email lookup.
- **Nothing reaches the sending tool without a human.** A run ends with a table of openers waiting to be read. Only the rows a person approves are staged, with the opener as that person left it, into a campaign that cannot send until someone launches it.

## 0. Check the sources before spending
- <tool:data_rows> — your watch list, filtered to active: the competitor, its customer-page URLs, its detectors, and the positioning to use only if a prospect raises the competitor in a reply. A second competitor is added here, not in the process.
- <tool:data_rows> — the accounts table, keyed on domain, **before detecting anything**. Without a CRM in the loop this table is the memory: a row already worked is not worked again.
- <tool:apify_actor> — free, takes `actor_id`. Confirms the token and the crawler actor exist. It reads the actor's card, not a permission to run it: a scoped token can still refuse the run itself (a 403 on actors that execute custom code is the usual case), so **a 403 on the first `apify_run` is the no-go**, reported as such. A dead token makes the run a no-op, and saying so is the correct outcome, not a thin list presented as a result.
- <tool:dropcontact_credits> — free. A zero balance discovered halfway through step 5 produces a half-enriched batch that looks complete.
- <tool:lemlist_get_campaign> — the template campaign for each persona and language answers on this key. Then <tool:lemlist_sequence> with `op="get"` returns every step with its copy: **write down every variable the copy references**. A lead missing a variable is held back at send time, silently.
- <tool:lemlist_campaign> — `op="reports"` on each template: `state` must read paused, or the review lock must visibly hold (`reviewedCount` and `inSequenceLeadCount` at 0 against `totalCount`). **Anything else stops the run here**, naming the campaign. A campaign with auto-review on turns `lemlist_create_lead` itself into a send, and no amount of care downstream gets that email back. Keep the templates as campaigns nobody launches; step 8 stages into a duplicate.
- Some people-data balances cannot be read ahead (a brokered key returns no balance). Step 5's people search is then the one paid read with no budget check before it runs, which is why its caps are hard numbers.

## 1. Read the competitor's customer pages
This is the detector.
- <tool:apify_run> — the public `apify/website-content-crawler` actor on the competitor's customer page, with `maxCrawlDepth: 1`, `includeUrlGlobs` scoped to the case-study path (for example `https://<competitor domain>/customers/**`) and `max_total_charge_usd` on every launch. **Run it asynchronously.** A synchronous run returns for a single page but times out through a gateway on a real crawl, while the crawl itself completes fine. Keep `defaultDatasetId` from the launch response.
- <tool:apify_run_status> — poll until `SUCCEEDED`; `usageTotalUsd` is what it cost. An actor listed as free carries no per-result fee but still consumes compute units, so it is cheap, not free.
- <tool:apify_dataset_items> — read the pages as text.
- **The crawler will not read a logo wall or a card grid.** It extracts article prose and discards link markup, so the index page comes back as hero copy and statistics with no customer names. `saveHtml` returns the same processed HTML, and a browser crawler type does not help because it is not a JavaScript problem. What works is following the links: depth 1 plus the path glob returns each case study as prose, which carries far more than a logo does: the company, its scale, the systems it runs and often a named executive. A selector-based scraper that runs your own page function can read the grid directly, if your token is allowed to run actors that execute custom code.
- **Check for the cookie banner after every crawl.** Some case studies come back as the consent modal and nothing else, and the crawler's cookie-removal option does not prevent it. That is a silent loss on a run that reports success. Count the pages whose text is under about 400 characters or contains the consent wording, and re-fetch those URLs one by one. **Never report a page count as an account count.**
- A logo or a mention can be a partner, an investor or a reseller. Count a company only when the page says customer or the case study describes its use. Re-crawl every run instead of trusting last month's rows, and report what is new against the table.

## 2. Corroborate, never discover
Each source below raises a `corroborations` count on a row step 1 created, and keeps the URL a human could open. **A hit with no URL is `unsourced` and is not written as evidence.**
- <tool:theirstack_jobs_search> — `extra={"job_description_pattern_or": ["<competitor>"]}`, one country in `job_country_code_or`, `posted_at_max_age_days=30`. The pattern is a full-text scan: short windows on one country return in seconds, while a year or several countries at once time out. **Most hits are the competitor's own recruiting ads.** There is no negative company filter (the not-equal name filters are rejected), so drop the vendor's own rows after the call. As a discovery source this yields almost nothing, which is exactly why it only corroborates.
- **Do not look for the competitor as a technology slug.** Many niche vendors are simply not in the taxonomy: zero companies on its slug while an adjacent tool in the same category returns thousands means "not indexed", not "no users". The nearest fuzzy slugs are unrelated products, and a fuzzy match returns the wrong companies with confidence.
- <tool:serper_search> — first `query='"<competitor>" -site:<competitor domain>'`, with `country` and `language` set to your market (both default to French). **A vendor's off-site footprint is mostly its channel**: press releases announcing a joint offer, integration partners, resellers. Partners and resellers are a hard disqualifier here, so this sweep's main output is a drop list. Write them to the accounts table as dropped, with the reason, so the radar stops rediscovering them.
- <tool:serper_search> — then the review sites directly, one query per site with `site_filter` (`g2.com`, `capterra.com`, `trustradius.com`). This is the sub-source that finds real users: reviews are written by customers, dated, and name the reviewer's role and company size. Classify every hit as **user, partner, reseller, or vendor marketing syndicated onto another domain**. Only a user corroborates.
- <tool:apify_run> — a profile-posts scraper actor from the Apify store on the competitor's company page, for post engagement. Most of that audience is the vendor's own staff, partners, recruiters and competitors, and a reaction is not evidence of use. What is worth keeping is **comment text**. A person describing their own problem in public, this month, is the best opener material this process produces, so keep it verbatim on the row it corroborates. These actors bill for zero-result queries too, so cap the post list instead of pointing it at a year of history.

## 3. Resolve the firmographics
Two providers, answering different questions.
- <tool:linkedin_aiark_search> — `op="companies"`, `account={"domain": {"any": {"include": ["<domain>"]}}}`: `domain` takes a plain list, while `name` takes the `SMART` wrapper, and they are not interchangeable. Never filter on `website`, which used to be silently ignored and return the whole index as your result. It returns staff count, industry, **a revenue band**, headquarters country and the record's `id`.
- **Vet every record on staff count before using it, and keep what you vetted.** A domain search can return several companies sharing the string (a tiny unrelated firm, a regional branch), and the top result is not always the one that owns the domain: a foreign subsidiary can hold the `.com` while the parent sits on a country domain. Write the chosen record's `id` and its staff count on the account row. Step 5 searches people on that record, and the next run reuses it instead of re-picking among look-alikes.
- **A revenue band can straddle your floor.** Write the band on the row, do not resolve it either way, and let step 4 renormalise over the other components. Guessing the midpoint is how a real account gets dropped. Never infer revenue from headcount.
- <tool:theirstack_companies_search> — `extra={"company_name_partial_match_or": [...]}` for `employee_count`, `industry` and `domain`. The typed `company_names` argument is case-sensitive, so prefer partial match. **Keep `limit` at 5 or below**: `technology_names` on a large company runs to thousands of entries and blows past any context limit, and billing is per company record anyway. An empty `data` is a normal result on small companies, so do not retry. Batch a few names at a time and merge on domain, because near-duplicate records are the norm.
- **Entity traps to write down, not trust past**: the record is the wrong legal entity carrying the right revenue; only a subsidiary holds the domain; a holding or investment company reads far too small on headcount; the same domain returns twice with different industries. Where the two providers disagree on headcount, keep both on the row. A row whose entity cannot be resolved stays unscored with the problem written down (`entity unclear`) instead of being scored on another company's numbers.

## 4. Score against your account band
No provider is called; nothing is bought. The weights below are a starting weighting, to be re-fit on your own replies once a few waves have come back.

| Component | Weight | What it reads |
| --- | --- | --- |
| Revenue | 0.25 | Above **[your revenue floor]** |
| Headcount | 0.20 | Above **[your headcount floor]** |
| Country | 0.20 | Headquarters in **[your countries]** |
| Sector | 0.20 | In **[your sectors]** |
| Evidence strength | 0.15 | How the account was detected, and its corroboration count |

- Where a component has no data, note it and renormalise over the rest, and re-score older rows when a new component gains a source, since scores on different component sets are not comparable.
- **The bar is a real bar**: **[your bar, e.g. 0.5]** on this scale. Below it, the row is dropped with its reason.
- **Hard disqualifiers**: under a floor (except a holding company judged on headcount alone), headquarters outside your countries, the competitor itself, its resellers and integration partners, and any account you already sell to. Ask whoever owns the customer list for an exclusion list before the first send, and keep it as dropped rows.

## 5. Find and enrich two or three buyers
Two paid reads share this step because the second only ever runs on what the first kept: people first, then a work email for the ones who survive. Only accounts that scored reach it. Read your personas table first: which titles are a first contact and which are a second.

**Find the buyers**
- **Take the free contacts first.** Executives quoted by name and title in the case studies from step 1 are already at a target account.
- <tool:linkedin_aiark_search> — `op="people"`, **filtered on the record step 3 vetted, never on the bare domain.** A domain alone brings back the people of every look-alike step 3 threw out, and you pay for each of them. The account filter documents `domain` and `employeeSize`, so pass both: `account={"domain": {"any": {"include": ["<vetted domain>"]}}, "employeeSize": {"type": "RANGE", "range": [{"start": <about half the vetted staff count>, "end": <about double it>}]}}`. If your account filter accepts the company `id` directly, filter on that instead, but confirm the key first with one `size=1` call: an unrecognised key is dropped silently, the way `website` was, and returns the whole index as your result.
- **Only `seniority` and `location` are applied as contact filters.** `title` and `department` are refused, because they used to be accepted and silently dropped: a department filter that appears to work returns the same unfiltered people in the same order.
- **Seniority is the cost lever**, since billing is per record returned. Tier A is `c_suite` only, small and cheap, and contains the economic buyer; fetch it in full. Tier B is `director`, `head` and `vp` for the operators, which is large, so cap it at two pages of `size=100` per account and write `pool_completeness` (exhausted or sampled) on every row. Seniority is normalised from the title and misses people in sectors whose titles do not follow the hierarchy, so use it to shrink the pool, never as the selection itself.
- The filter shape is `contact={"seniority": {"any": {"include": ["c_suite"]}}, "location": {"any": {"include": ["<country>"]}}}`. Put the country on `contact.location`, not `account.location`, which means headquarters and returns zero for a company headquartered elsewhere.
- **Always pass `fields=["id", "profile", "link", "location", "department"]`.** Without it the company's whole record is repeated identically on every person, and one page of 100 runs to millions of characters. Since that projection drops the company block, **write the vetted account `id` on every contact row from the query itself**, so each person stays traceable to the record you checked.
- **Rank on the job title first, and use `department` only to break ties.** The department classifier is wrong in both directions: a senior executive whose title contains an unrelated word (a business-unit name, say) gets filed under the wrong department, and a staff representative gets filed as a senior executive in your buyer's department. Read the title for what it excludes too: recruiting, talent acquisition and staff representation are not buyers, whatever the department says.
- **Check the current employer on every row.** Read `profile.headline`: some records link people to a company they have left, with no flag. If the headline names another employer, drop the person. This is also the backstop for anyone at a look-alike company the account filter let through. When several people at one account name a company the account record does not, the account has probably been renamed. Note it on the row, because an opener addressed to the old name dates the whole message.
- **Two or three per account, never more.** A fourth and fifth contact at the same company reads as a blast and gets noticed internally. Write `language` on every row: it decides which template the lead is staged from.

**Enrich the survivors only**
- <tool:dropcontact_enrich> — one batch call for all the survivors (up to 250 contacts), **grouped by company domain** inside the batch, with the domain passed as `website` on each contact, your row key in `custom_fields` (echoed back unchanged, since results are not guaranteed to come back in order) and `language="en"` when the contacts are not French, since French processing is the default. Dropcontact resolves an address pattern per domain, so the people of one company submitted together come back more reliably than the same names sent one at a time.
- <tool:dropcontact_result> — the call is asynchronous. Poll about 30 seconds after submitting, then every 20 to 30 seconds until `done`. **Never write the first response as the result**, or empty emails overwrite good rows.
- Keep a `nominative@pro` address. A `generic@pro` mailbox is not a person, anything `@perso` is never used, and `invalid@invalid` counts as no email. **Never fall back to a personal address.**
- Stay on this provider for the motion: these are senior people at large accounts, exactly the population that notices where its address came from. <tool:lemlist_enrich> is a different provider chain, used only where Dropcontact returned nothing and the account scored in your top band, with `find_email` and the person's name and company domain. It is asynchronous too: collect it with <tool:lemlist_enrich_result>, first after about 15 seconds, then every 15 to 30 seconds until `done`, and poll once more on a `done` that carries a warning and no payload. Record `email_source` either way.

## 6. Write the opener for review
**Never name the competitor in a message.** Not as a comparison, not as a compliment. Naming the incumbent tells the reader they were scraped and drops you into a comparison you did not choose. The positioning in your watch list is for the moment a prospect raises the competitor themselves in a reply.

**Write to a solved account, not a prospect.** These companies bought a tool and rolled it out, so an opener implying they are exposed is visibly wrong and burns the account. The angle that works is **the edge the incumbent does not cover**: **[a country, a system, a product line or an entity outside the typical rollout]**. That edge comes from your own product knowledge, not from this process. Write one paragraph naming where **[competitor]** stops before the first send. Without it the radar still produces a clean, evidenced account list, but nobody can write the email.

The opener hierarchy, strictly:
1. **What the person said**: a public comment, paraphrased back. Dated, verifiable, their own framing.
2. **What their company published**: the case study's own subject, named specifically (the process it changed, the scale it gave).
3. **A job ad**, rarely, and carefully: quoting a posting back at someone reads as surveillance.
4. **Nothing else.** A row with none of the three should have been dropped in step 4.

- <tool:data_write> — the person's opener on their contact row, as plain text, with the evidence it rests on (the URL and the quote) next to it so the reviewer can check it without opening another tool. The row moves to `to review`. Plain text matters later: sequence copy is HTML, and an unescaped ampersand or angle bracket breaks the markup around it.
- **Nothing is written to the sending tool in this step.** A lead that exists in a campaign before anyone has read its opener is one misconfigured setting away from being sent.

## 7. A human approves, name by name
- <tool:data_app> — the contacts table filtered to `to review`, rendered inline as a searchable table a person reads in the chat. It is a read view: approvals, rejections and opener edits are made on the row itself, through the dashboard link on the card. No messaging tool is needed for the gate: the review happens where the copy already is.
- A person moves each row to `approved` or `rejected`, and **every rejection carries its reason**. The reasons are the only thing that improves the next run. An edited opener is saved on the row, and the row is what step 8 reads, so the edit is exactly what gets staged.
- **Rejected**: the row stops. It is never staged, and the account keeps its evidence for the next run.

## 8. Stage the approved leads
Runs once the reviewer says the review is done, in Claude ("stage the approved rows").
- <tool:data_rows> — the contacts at `approved` with no lead id yet. A row approved last week and already staged is never staged twice.
- <tool:lemlist_campaign> — `op="duplicate"` on the template matched on persona and `language`, named for the competitor and the week. A duplicate is born paused and carries the template's sequence, so the variables checked in step 0 still apply. Never stage into a campaign someone has launched: its leads would join a live sequence before the person who approved them chose to send.
- <tool:lemlist_create_lead> — one call per approved person, with the approved opener and every other variable the copy references in `custom_variables`, instead of patching variables one call at a time. Write the returned lead id on the row, which moves to `staged`.
- <tool:lemlist_add_lead_variables> — only when a reviewer changes an opener after it was staged: rewrite that one variable on the lead id, and never re-create the lead.
- <tool:lemlist_campaign> — `op="reports"` after loading: `totalCount` equal to the rows staged, with `reviewedCount`, `inSequenceLeadCount` and `emailsSent` at 0, proves nothing is queued to send, without sending anything. A count above 0 means the duplicate carried auto-review over from the template: that goes first in the report, before anyone opens the campaign.
- **`lemlist_launch_lead` and the campaign start are never called.** They are the tools that make an email leave, and keeping them out of every step is what leaves the last gesture to a person.

## Output
The weekly run reports, in this order: accounts on the customer pages and how many are new since the last run; pages re-fetched after a cookie banner; corroborations added by source, and partners dropped; firmographics resolved, how many carried a revenue band, and entities left unclear; accounts out of band, by criterion; contacts sourced per account with `pool_completeness` per tier and people-data records consumed; enrichment hit rate per provider and credits spent; unreachable contacts; and the count waiting for review. The staging pass reports approved and rejected counts with the rejection reasons, the campaign each lead went into and its paused state from the reports call. Both end with one line naming anything the run could not do (a timed-out search, a scoped token, a balance that ran out mid-step, a template that was not locked). A radar that hides its own gaps is worse than one that found less.