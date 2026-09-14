# Build a prospect list from a one-sentence description

**When to use it**: someone can describe the companies and people they want in one sentence, and the alternative is a morning of guessing filters in a database, exporting rows and spot-checking a dozen of them by hand. The run turns that sentence into a scored, deduplicated, enriched list and a paused campaign. Every step is a gate on the next: nothing expensive is bought for a row that has already failed a cheaper test, and a person launches.

```
              Natural language input in Claude
              "Logistics companies in France, 250 to 1,000 employees, hiring supply chain roles. Source 40 accounts."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Read the sentence back as filters          ║   nothing is spent before a yes
║  Headcount band, sectors, countries and         ║
║  personas printed and confirmed first.          ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Source accounts, minus what the CRM holds  │   apollo_search_organizations, hubspot_object
│  Count first and cast wide, then customers and  │   id, name and domain come back, nothing else
│  open deals leave on a free domain match.       │   one credit per page of 100, CRM reads free
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already known     customer or open deal
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Enrich and tier what survived              │   apollo_bulk_enrich_organizations
│  Firmographics bought per company, capped to    │   one credit per company, ten per call
│  the wave, off-vertical rows out, three tiers.  │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ off-vertical      outside the brief, no stated reason
                         ├───────────────▶  ▪ bottom tier       not worked any further
                         ▼  top two tiers only
┌─────────────────────────────────────────────────┐
│  4 · Pick two or three people each              │   apollo_search_people
│  People matching the persona at each company,   │   one domain per call
│  capped so no team gets a blast.                │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no contact        nobody fits at this company
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Buy signals for what survived              │   theirstack_jobs_search, serper_search
│  Open roles, dated news and the company's own   │   apify_run_sync, top tier only
│  pages, each finding with its URL.              │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ acquired          absorbed into a group, with the URL
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Find verified emails and numbers           │   apollo_match_person
│  The database answers first, a waterfall takes  │   fullenrich_enrich_linkedin on a miss
│  the misses; known people leave on the address. │   hubspot_object, lemlist_lead on each address
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ known person      dead lead status or in a sequence
                         ├───────────────▶  ▪ unreachable       no email or profile, name kept
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Write each opener from a signal            │   never an invented hook
│  One dated finding opens the email, or a        │
│  plainer version claims less.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Stage the campaign, update the CRM         │   lemlist_campaign, lemlist_create_lead
│  A paused campaign with per-person openers, and │   hubspot_object, hubspot_list
│  CRM records carrying the evidence.             │   leads held for review, a person launches
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

## The spend rule, which governs every step
A company search bills one credit per page of up to 100 results and a CRM read is free. A company enrichment costs one credit **per company**, the same as revealing one person, and batching saves calls, not credits. People search bills per page, and this process runs one page per company. Signals are billed per record or per query, and a reveal costs its credit whether or not it matches. So every free test runs before the first per-company charge, and every per-company charge runs only on what the cheaper tests kept. If you find yourself enriching a company before the CRM check, or buying signals for a company with no fitting contact, the order is inverted and you are paying to learn something you were about to throw away.

Write every row to a working table as it moves, with <tool:data_write>, one row per person keyed on `<domain>:<person id>`. People search returns obfuscated last names, so a name-based key cannot be computed until a credit has been spent to reveal it; the search result's id is stable and free. A run that stops on a quota then resumes where it stopped instead of re-buying what it already has.

## 1. Read the sentence back as filters
- Write out the structured filters you inferred before running anything: headcount band, sectors, countries, seniority and job functions, the outreach language and who owns the records. Print them and wait for a yes. "Mid-sized" read as 50 to 250 when the person meant 250 to 1,000 is a different list, and finding out afterwards means paying for the wrong one twice.
- Anything the sentence did not specify stays unset. Never add a country filter because most of your customers happen to be in one.
- Build the title list from your own persona definitions, in every language the countries imply, plus an exclusion list: assistants, recruiters, interns, freelancers and **[titles that share a keyword with your buyer but are not buyers]**. A buyer keyword inside a partnerships, sales or product title is the classic false positive.
- If the sentence implies a language your persona definitions don't cover, stop and ask for the titles rather than inventing them or silently falling back to another language.
- Size the wave against what your keys can carry before anything starts. On a metered key, a run that runs out halfway leaves half a cohort in the CRM and half a campaign in the outreach tool, which is worse than not starting. State the arithmetic (one enrichment credit per company kept, one reveal per person) and let the person pick the size.

## 2. Source accounts, minus what the CRM holds
- <tool:apollo_search_organizations> — run the filters with `employee_ranges` and `locations`, and look at the total before pulling pages. Under about 50 results, the filters are too narrow: say which one to loosen rather than proceeding with a list nobody can run a campaign on. Over about 2,000, report the count and ask before spending anything.
- **A wide net costs the same as a narrow one.** Billing is per page of up to 100 results, not per filter. Split a vertical into several single keywords rather than one compound phrase, which returns almost nothing, and narrow afterwards.
- **The search returns only `id`, `name` and `primary_domain`**, whatever `fields` asks for: no industry, no headcount, no country. The band and the location are enforced by the filter, not read back. A `fields` list that names a value the endpoint doesn't return is accepted and silently left empty, which is how blank firmographics reach a CRM, so ask for those three and page at `per_page=100`.
- <tool:hubspot_property> — read the field definitions before the first CRM call of the run: property keys belong to each workspace, and hardcoding them is how a run reads or writes into nothing.
- <tool:hubspot_object> — match each company on its normalised domain (lower-case, no scheme, no `www.`, no path) with an `EQ` filter: HubSpot treats two spellings of one domain as two companies, and a name match will happily return a different company. Read the company's deals with `op="associations"`, then `hs_is_closed` and `hs_is_closed_won` on each. **An existing customer or an open deal ends the row** before a credit is spent on it: a customer is `lifecyclestage` = `customer`, any closed-won deal, or **[your customer property]** if you keep one. Keep the CRM company id for everything that stays, so step 8 updates instead of duplicating.
- These reads are free and the next step is billed per company, which is the entire reason the check sits here. Count what survives against the size agreed in step 1; this is the last free moment to find out the wave is too big. Search pages 100 records at a time with `after`, and a private app is capped at roughly 190 requests per 10 seconds, so pace a large batch rather than retrying into the limit.

## 3. Enrich and tier what survived
- <tool:apollo_bulk_enrich_organizations> — ten domains per call, **one credit per company**, only on what step 2 kept and never past the wave size agreed in step 1. It returns the industry, headcount, department split and funding history the tier reads.
- **The result set will not be the vertical you asked for.** Keyword tags are applied loosely, and a search for one vertical comes back with companies from others; the search can't show you which, because it returns no industry. Filter on the enriched industry now, and give a reason for anything that survives outside it. A noisy keyword means some of these credits buy companies that are thrown away here, which is one more reason to keep the wave close to what the campaign needs.
- A domain the enrichment doesn't match is not bottom tier by default: keep it in the middle tier with its fields empty and a note, and fill them from the reveal in step 6.
- Tier every company on that record alone, since no signal has been bought yet. Band into three tiers and stop working the bottom one entirely. Don't score from the revenue figure: at mid-market sizes it is often off by an order of magnitude.

## 4. Pick two or three people each
- <tool:apollo_search_people> — **one domain per call**, top two tiers only. Passing several domains returns a single relevance-ranked page in which one large company crowds out the rest, and coverage per account becomes luck. Pass `titles`, `seniorities` and `person_locations`: a domain is worldwide, so a national subsidiary searched by domain alone returns people in every country the group operates in.
- Apply the exclusion list in memory, because the endpoint has no exclusion filter. Then keep at most two or three people per company. More than that and you are emailing a department, which gets noticed internally and reads as a blast.
- Results carry no email and an obfuscated last name. Keep each result's `id`: it is the only reliable handle for the reveal in step 6.
- A company with no fitting contact is not a target, whatever it scored. Mark it and spend nothing further on it.

## 5. Buy signals for what survived
Only for companies that still have a fitting contact from step 4. Three sources, each answering something the others cannot.
- <tool:theirstack_jobs_search> — open roles per company, addressed with `extra={"company_domain_or": ["<domain>"]}`, with `posted_at_max_age_days` bounding the window and `limit` bounding the spend; add `full=true` when you need the job description, which the default projection drops. A company hiring for the function your product serves is the strongest single signal there is: dated, public and carrying a URL. The stack cuts both ways: a tool you integrate with is a reason to call, and a direct competitor already installed is a reason not to.
- Expect three things. **An empty result is normal**, not an error: many mid-size companies have no record, so don't retry. **One domain can return duplicate company records** with different headcounts, so take the union. And **the stack misses layers**: where a layer that sits on top of a system appears with nothing under it, the system is almost certainly present but unnamed, and the contact database's own technology list is a usable second opinion. The response carries no credit counter, so the run counts its own calls against **[your per-run budget]** and marks the remaining companies as partial when it is reached.
- <tool:serper_search> with `kind="news"` and `tbs="qdr:y"`. An unbounded news query returns a funding round from four years ago as readily as one from this quarter, and it scores as a live trigger. Set `country` and `language` explicitly, because the defaults are not your market. Run a few query families per company (funding, a leadership change in the function you sell into, expansion or new sites, plain company news) at `num=5` each.
- **Re-check independence here, because this is the only step that can.** Search explicitly for the company being acquired, merged or absorbed. Firmographic records go stale, and a company folded into a group last quarter can still top the batch. If it has been, stop with the source URL: no score survives that.
- <tool:apify_run_sync> — top tier only, where search returned a thin result: run a hosted actor against the page the result points at, such as the careers page or the company's own profile. Find the actor with `apify_store_search` and read its defaults with `apify_actor`. Its input fields are specific to each actor, and an invented field name bills and returns nothing. Bound the spend at launch with `max_items` and `max_total_charge_usd`; past 300 seconds the call answers 408, so switch to `apify_run` and `apify_dataset_items`. A failed or empty run is a normal result and never blocks the step.
- Write every finding with its source, type, detail, URL and capture date. **The URL is what lets a person check a claim before repeating it in an email.** A company with nothing found keeps its score and is recorded as having no signal. No signal is allowed; an invented signal is not.
- Combine fit and signal strength into hot, warm and cold with a rule you write down, for example hot when both clear **[a high bar]** or one clears **[a higher bar]** and the other **[a middle bar]**, so two runs tier the same company the same way.

## 6. Find verified emails and numbers
- <tool:apollo_match_person> with `person_id` from step 4, never first name plus company, which matches nobody and is charged anyway. A response carrying `person._stub: true` is a failure, not data. On a shared key, read `platform_quota.remaining` on every call and stop while the rows are still consistent.
- The reveal's `person.organization` block carries the employer's `industry`, `country` and `estimated_num_employees` at no extra cost. Take them once per company, from the first person revealed there, for any company step 3 could not enrich, rather than writing it to the CRM with blank firmographics.
- **Read the contact layer, not just the person layer.** A record can report the email as verified at the person level while its own contact block carries a hard-bounce reason and no deliverable address. That address has already bounced. An address is usable only when both layers agree, and the outreach tool will send to whatever it is handed.
- <tool:fullenrich_enrich_linkedin> — the misses only, in **one batch job** of up to 100 contacts, each with `first_name`, `last_name`, `linkedin_slug` (the slug, not the URL) and `domain`, and `enrich_fields=["contact.work_emails"]`. Collect with <tool:fullenrich_result> after about 30 seconds, then every 20 to 30. **The waterfall is not an independent check.** Compare what it returns against the address the first provider rejected: a fallback that hands back the same dead address, with no sign that it is dead, is not a second source.
- **Check every address before it goes further**, since this is the first moment one exists. <tool:hubspot_object> — search contacts on `email` `EQ`: a match keeps its contact id for step 8, and a lead status in the set of dead statuses you agreed up front ends the row. <tool:lemlist_lead> with `op="get"` and the address — the global lookup across every campaign, so a hit means a colleague already has this person in a sequence, and the row ends too. Both checks are free; the reveal before them is the one credit this ordering cannot save.
- **Phone data promises more than it delivers.** A search can report a direct line for almost everyone while the matched record returns the company switchboard, identical for every contact at that company. Only a mobile or direct number is a real number, and a switchboard is written labelled as one. Ask the waterfall for `contact.phones` only if the list will actually be called, since phones cost ten times an email.
- Record where each address and number came from; `none` is the right value for a bounced address. Every row starts at `not_attempted` and leaves it only when a resolver was actually called, and a <tool:data_rows> count of rows still `not_attempted` must return zero before the step is done. A person with no email and no profile is unreachable, but the row stays, because a name and a title are enough for a call.

## 7. Write each opener from a signal
Two tiers. Where a company carries a signal, the opener names that finding and the CRM note keeps its URL. Where it does not, the plainer version goes out and claims less. A plain email is the better failure mode than the vague gesture at relevance that makes outbound read as automated.
- The opening detail comes from a captured finding on **that** company. No hook is invented to fill the slot.
- An observation about their business, never a compliment.
- Plain text: no markup, no links, no signature, which is a step a person adds. Sequence copy is usually HTML and a variable is dropped in verbatim, so an ampersand or an angle bracket breaks the markup around it.
- Customer stories and figures come only from **[your list of citable customers and numbers]**; a claim with no source is not made.
- Some findings are true, relevant and still not openers. A restructuring or a disclosed weakness reads as an attack in a cold approach: keep it in the notes as material for the second conversation.

## 8. Stage the campaign, update the CRM
Two writes, and neither sends anything.
- <tool:lemlist_campaign> — `op="create"` lands the campaign **running**, so `op="pause"` is the very next call, before any lead. Add **[your sequence steps]** with <tool:lemlist_sequence>, then read the copy back and write down every variable it references: a lead missing one is held back rather than sent. Read `op="statutes"`, where level 3 blocks a launch.
- <tool:lemlist_create_lead> — every variable in `custom_variables` at creation, since patching several at once has applied only the first, with `deduplicate=true`. **The lead lands held for review.** Then `lemlist_campaign` `op="reports"`: `totalCount` up by the wave, with `inSequenceLeadCount` and `emailsSent` unchanged, proves nothing is queued to send, without sending anything.
- <tool:hubspot_object> — upsert the company with the id kept in step 2, then the contact with the id kept in step 6. A new contact is created with its `associations` to the company, which the tool accepts only on `op="create"`; an existing contact's company link is left as it is. Then attach the signals and their URLs as a note in the HTML subset it renders (`h4`, `p`, `b`, `ul`, `li`, `br`, `i`). Notes cannot be updated, so check whether this process already wrote one for the contact before a re-run adds a second.
- When a property this run writes doesn't exist yet, create it with `hubspot_property` `op="create"` and set both `type` and `fieldType`, which are separate axes: a tier is `enumeration` with `select`, a yes/no flag `enumeration` with `booleancheckbox`, a score `number` with `number`.
- <tool:hubspot_list> — one list per wave, named after this run, holding every contact written, so the cohort can be found and worked as one.
- Then stop. Launching is a person's decision: they read the queued leads in Lemlist, resume the paused campaign and release the leads with `lemlist_launch_lead`. The run never calls that, `lemlist_campaign_start` or `lemlist_campaign_auto_review`, which would make every added lead send on creation.

## Output
Report: the filters inferred, companies found, how many were dropped as already known, how many were enriched and how many left as off-vertical or bottom tier, contacts sourced, how many companies carried a signal and how many did not, how many were set aside as acquired, the enrichment hit rate split by provider, how many revealed people were dropped as known and why, how many addresses were rejected as undeliverable, credits spent per connector against the size agreed in step 1, and the campaign's paused status with its leads held for review.