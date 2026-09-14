# Turn job postings into outreach campaigns by persona

**When to use it**: the people who have your problem rarely post about it, but their companies do advertise the hire they are making to deal with it. A job posting says two things at once: **who owns the decision** (a company hiring the head of a function has nobody in that seat, so the CEO owns it today) and **what to talk about** (the duties in the ad are the problem, in the company's own words). This process turns those postings into two or three named people per company, each loaded into the lemlist campaign written for their persona, held for a person to launch.

```
              Scheduled routine, every morning
              "Find the companies that posted an operations lead role this week and queue their leaders for outreach."
                         │
                         ▼
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  1 · Postings for your titles    │  │  2 · Yesterday's job ads         │   apify_run_sync
│  TheirStack jobs on a measured   │  │  A saved search scraped daily,   │
│  title set, description kept.    │  │  deduplicated on the job id.     │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼  merged on the company domain
┌─────────────────────────────────────────────────┐
│  3 · Strip intermediaries and past contacts     │   data_rows
│  Recruiters, job boards, your excluded accounts │   lemlist_campaign
│  and anyone a campaign ever enrolled.           │   match on domain, never on name
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ excluded          intermediary, account or already enrolled
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Qualify on the posting text                ║   serper_search
║  The duties describe the problem you solve, at  ║   the posting is the evidence
║  a real company that is still independent.      ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ disqualified      wrong problem, fake poster or acquired
                         ▼  a qualified domain, recorded exactly
┌─────────────────────────────────────────────────┐
│  5 · Name two or three personas                 │   linkedin_aiark_search
│  The hiring title says who decides; AI Ark      │   titles matched in code, not filtered
│  names them, sized on its own headcount.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nobody named      no persona inside the size band
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Find an email for every person             │   linkedin_aiark_person
│  AI Ark first, FullEnrich for the misses, a     │   fullenrich_enrich_linkedin
│  domain gate, and a count that must be zero.    │   fullenrich_result
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ held              address on an uncorroborated domain
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Write the variables per person             │   a checker shown to catch planted defects
│  An opener from this posting and an angle per   │
│  persona, checked as composed emails.           │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ watching          no specific fact to open on
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Load one campaign per persona              │──▶  lemlist campaign  one per persona, emails and a connection request
│  Each lead lands held for review, its variables │──▶  people table  lead id, persona, stage queued
│  read back before the batch is called done.     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  9 · A person reviews and launches              ║   the only step that sends, never the agent
║  Every lead is read before it is launched,      ║
║  and positive replies go to your CRM.           ║
╚═════════════════════════════════════════════════╝

▪ terminal — the row stops there and nothing further is spent on it; excluded companies join the exclusion table
```

## Before the first run
- **Measure the title set before trusting it.** The job titles you source on are the precision lever of the whole process. Pull a sample of 25 postings for a candidate set and count the clean hits (a real employer, in your size band, with the problem) against recruiters and large enterprises. Broad technical titles pull staffing agencies in volume; a title that only exists on early teams (a "founding" role, say) is a better size proxy than any headcount filter. Keep **[your measured title set]** in one place.
- **A persona map**, one row per hiring title: the two or three personas to contact (the one who owns the decision first, then the ones who share it), a fallback for each when that seat is empty, and the angle each persona leads with. Three rules override the table: when the posting *is* the leadership role, go one level up (hiring a head of the function means the CEO or a founder); below **[your small-company headcount]**, contact the CEO or a founder whatever the row says; a title no row covers leaves the flow, logged with its exact wording. **The title set and the persona map are one setting**: a title step 1 sources that no row maps is a dead rule, and a row whose titles step 1 never sources is one too. When one moves, re-read the other.
- **One lemlist campaign per persona.** Build each once with `lemlist_campaign` `op="duplicate"` from a template campaign someone has reviewed: a duplicate is born paused, whereas `op="create"` returns a campaign whose status reads "draft" while it is already running. Each sequence carries the email steps and a `linkedinInvite` step that always carries a note — a blank invitation converts far worse than one with a line of context.
- **Three tables**: people (one row per person keyed on the LinkedIn profile URL, company rows keyed on the domain), exclusions (one row per domain never to contact: customers, former customers, open deals, partners, competitors, known intermediaries, your own company), and a run ledger keyed on the date a run covers.

## 1. Postings for your titles
- <tool:theirstack_jobs_search> — `extra={"job_title_or": [title set], "min_employee_count": [floor], "include_total_results": true}`, `job_country_code_or` one region per call, `posted_at_max_age_days=90` on a full sweep and `3` on the daily run, and `full=true` so each record carries the description and the company object. `limit` is the spend bound: billing is per company record returned.
- **Probe with `limit=1` first.** An exhausted balance returns an empty result, not an error, and company credits are a separate, smaller pool that can run out mid-wave.
- **There is no working upper headcount bound here.** `max_employee_count` is accepted by the jobs endpoint and does not filter, and TheirStack's own headcount is wrong in both directions. The real size gate is AI Ark's `staff.total` in step 5.
- **Keep the description whole.** It is the raw material for the opener in step 7; a truncated excerpt loses the duties and leaves the opener to be rebuilt from the title alone.
- Drop every domain already in the people table before paging deeper.

## 2. Yesterday's job ads
- <tool:apify_run_sync> — a LinkedIn Jobs scraper actor on a saved search URL limited to the last 24 hours, with `max_items` and `max_total_charge_usd` set. Pick the actor on two tests: it returns the company's LinkedIn URL (step 5 matches on it exactly) and the full description. On the first run, list the fields it really returns: a rule that reads a field the actor never returns raises no error, it just lets everything through.
- **The URL is the real filter, and a keep-rule downstream can only remove.** Check the URL carries the location parameter (`geoId`): a browser applies your session's location silently, the scraper has no session, and the same URL returns another country's ads. Leave the keyword empty and filter titles in step 3, because a LinkedIn keyword matches the whole ad text, not the title. The job-function filter is the only truly restrictive one.
- Deduplicate on the job id against a log of jobs already seen. The same poster can republish one ad under a fresh id, so the id protects against reprocessing, not against a repeat offender: only the exclusion table catches that.
- Never try to recover a missing description by scraping the posting page: read without a session, it returns a login wall with a success status.

Steps 1 and 2 run in parallel as two sourcing lanes, and their results merge on the company domain, normalized (no protocol, no `www`, lowercase).

## 3. Strip intermediaries and past contacts
- **Titles first, on the title only.** Word-boundary matching, so a short token doesn't match inside a longer word, and never against the description, which mentions everything. Location from the ad, not the company's headquarters.
- **Recruiters have two tells.** A title that names the real employer after "at" is a recruiter reposting someone else's role — it recovers a name, not a domain, so resolve it with a search or drop it, and never guess a domain from a name. A TheirStack company record with more than 500 technologies is a job board republishing everyone's ads. The `is_recruiting_agency` flag cannot be trusted on its own.
- <tool:data_rows> — the exclusion table, matched on the normalized domain and on the company's LinkedIn URL where the table stores it (a domain changes, a company page rarely does). **Never on a display name.** Feed it from your CRM, or by hand until the CRM is connected, and remember what it can't see: it only knows what it was told, and it reasons by company, not by person.
- <tool:lemlist_campaign> — `op="export_leads"` on every campaign listed by `lemlist_list_campaigns`: everyone any campaign ever enrolled, drafts that never sent included. Persist the people and their domains instead of rebuilding the set every morning. A company that comes back with a new posting is the signal working, but it is never prospected twice for the same persona: only a posting that points to a different persona lets it back in.
- Anything excluded later, in steps 4 or 5, is added to the table too, so the same company costs nothing on the next run.

## 4. Qualify on the posting text
- **The duties must describe [the problem your product addresses]**, not just carry a matching title. Capture one specific fact verbatim (what the hire will actually do, the tools named, the team they join) into `signal_evidence`, and keep it in full.
- **Fake posters exist.** Disqualify, and add to the exclusion table, when the company name is itself a domain, the name and the domain don't resemble each other, the declared site is a link shortener or someone else's careers page, the site doesn't resolve, or no headcount, industry or address exists anywhere.
- <tool:serper_search> — one query for the company name plus "acquired". Job data and technographics both lag acquisitions by months. An acquisition, a shutdown or a rebrand is written as `stage="disqualified"` with its reason at the moment it is found: **a warning left in a notes field stops nothing.**
- A company selling what you sell is `disqualified` with reason `competitor`, and its domain joins the exclusion table.
- **The domain confirmed here is the answer key for step 6.** Record it exactly.

## 5. Name two or three personas
- <tool:linkedin_aiark_search> — `op="people"`, `account={"domain": {"any": {"include": [...]}}}` with about 25 domains per call, `contact={"seniority": {"any": {"include": [...]}}}` holding only the levels your persona map needs (`"founder"`, `"c_level"` and so on), `size` up to 100.
- **`title` and `department` are refused**: AI Ark would accept them, ignore them and bill the company's first page. Filter on seniority to cut pagination, then match titles in your own code, including non-English forms that an English-only match silently discards. Seniority is derived from the title, so use it to narrow, never to select.
- **The domain filter is loose**: assert `company.link.website` equals the qualified domain on every record. **Gate size on `staff.total`**, never on `staff.range`, which can place a small company a band or two too high.
- Exclude fractional and interim executives, and any title containing talent, recruiting, people or HR, checked on the title returned, not the one you asked for.
- One person per persona, so two or three per company, never more. When the persona isn't on the first page of a large company, page on or stop the row: never promote the least bad title on page one to be the target. A fallback taken is noted on the row, because that's the row a person reads first. Above **[a large-company headcount]**, stop and log it: paging stops being a strategy.
- A company where nobody was named is often recoverable before paying an index again: a plain search for the founder's LinkedIn profile or the company's own team page finds people the index doesn't have.
- Validate `first_name` as you write it (honorifics, nicknames in quotes, emojis, initials), and write every named person at `email_status="not_attempted"`.

## 6. Find an email for every person
- <tool:linkedin_aiark_person> — `op="export"` with `url` set to the person's LinkedIn profile, for **every** person row that carries one, not one per company: the second contact is not a spare, it's who replies when the first doesn't. Accept an address only with `free: false`, `generic: false` and `status: "VALID"`; a catch-all probe is `risky`, not `verified`. `{"found": false}` is a normal answer, not to be retried; a timeout raised as `retryable` is a failure, not an absence, because nobody was looked up. Records are large, so work in small batches and keep only the address.
- <tool:fullenrich_enrich_linkedin> — **one job** (up to 100 contacts) for only the people AI Ark missed, each with `linkedin_slug` (the slug, not the URL) and `domain`, and `enrich_fields=["contact.work_emails"]` only: a phone costs ten times a work email and this motion never calls. <tool:fullenrich_result> — first poll after about 30 seconds, then every 20 to 30.
- **The domain gate, on every address from either source.** A resolver infers the mail domain from its own company record, not from the site you qualified in step 4. Same domain: accept. Different domain: corroborate the organization with one `serper_search` (does it resolve to the same company, does the person's profile print it?) — corroborate the organization, never the shape of the string, since a lookalike on another extension can be an unrelated organization. Uncorroborated: `stage="held"`, both domains in the note, not loaded.
- **A disagreement is not a conflict.** FullEnrich returns *an* affiliation, not necessarily the current employer. A real conflict is only a different person's name, or a different operating employer with an executive title implying the person left. Advisor, investor, board, accelerator and community roles are not conflicts, and neither are absurd values. Resolve a real one against the live LinkedIn profile, never another bought index.
- **`email_status` has four values, and the default is the one that says nothing was measured**: `not_attempted`, `not_found` (both resolvers were called and returned nothing), `risky`, `verified`. Never write `not_found` on a person no resolver was called for: once written it is indistinguishable from a real miss forever, and the right response to a real miss (stop spending) is the wrong one for someone nobody checked. Record `person_source` (`aiark` or `fullenrich`) so the next wave reads real hit rates. Coverage follows company age and size; a zero on very young companies is the cohort, not a broken pipeline.
- A person with a LinkedIn profile and no email is not dropped: `channel="linkedin"`.
- **The completion gate** — <tool:data_rows> with `count_only=true` and `filter={"email_status": "not_attempted", "person_linkedin": {"not_empty": true}}` must return zero before the wave is called done. A non-zero count is the run telling you it skipped people.

## 7. Write the variables per person
- Five lemlist custom variables per person: `role_hired`, `opener`, `angle` (the persona's), `angle_followup` (something this particular company could hand over, which is where the personalization lives) and `li_note`.
- **`role_hired` is what a person would say, not the ad's title field**: two to four words, stripped of gender markers, parentheses, language requirements, contract, location and remote mentions, product lines, brand names and emojis. No `&` in any variable, because the sequence copy is HTML.
- **The opener is a first-person observation**, in one of two shapes: "Saw you're hiring a **[role]** to **[what that person will actually do]**." or "Came across the **[role]** role at **[company]**, with responsibilities including **[the work]**." Hundreds of openers that start the same way are fine when the rest of each sentence fits one company only: variety lives in the fact, not the grammar. The test is whether the opener could move to another company without becoming false. A second person at the same company never gets the first person's opener or follow-up angle, checked against the sibling's stored variables, not from memory.
- **Never diagnose the reader.** No sentence whose subject is this company's internal workings; state the problem as a general observation ("often", "usually"). Every variable ends on substance, never on an offer: the template already closes with one, and a variable that ends on another makes it twice. Any number comes from your own proof-points table. No rhetorical questions, no em dashes, sentences under 20 words.
- `li_note` stays under 300 characters and does not quote the job posting.
- **Check the composed emails, not only the variables.** Assemble each body from the template and the variables, and assert in code that no body contains the same sentence twice, that no banned phrase appears, and that every placeholder resolved. Then mutation-test the checker by planting each defect class in a passing sequence: a checker that has never caught anything has not been shown to work.
- A person with no specific fact to open on stays at `stage="watching"` with a note, not loaded.

## 8. Load one campaign per persona
Three checks before the first write of the morning, on each campaign:
- <tool:lemlist_sequence> — `op="get"`: the steps are the ones expected, and every variable the template references is one you fill. A referenced variable with no value holds the lead back silently.
- <tool:lemlist_campaign> — `op="statutes"`: `level` 3 blocks, 2 warns (a daily limit, or the sender's LinkedIn invitation quota already used up before the first lead of the day), 1 informs. Then `op="reports"`: `totalCount`, `reviewedCount` and `inSequenceLeadCount` prove the review gate holds without sending anything, since the last two stay at zero while the first climbs. On a paused campaign the counters prove nothing: read `state` first.

Then load:
- <tool:lemlist_create_lead> — one call per person, into their persona's campaign: `email` when step 6 verified one, `linkedin_url`, `first_name`, `company_name`, `company_domain` and `custom_variables`. Every enrichment flag stays `false`, since step 6 already did that work, and `deduplicate=true` is a last guard that skips the insert when the email already sits in another campaign. The lead lands held for review. **Never arm `lemlist_campaign_auto_review`**: with it on, adding a lead sends it.
- <tool:lemlist_lead> — `op="get"` on at least one lead per batch to read the variables as stored. A variable update has been seen to store spaces as `+` while answering ok, so correct a variable by deleting the lead (`op="delete"`) and creating it again, never by patching it.
- `lemlist_campaign` `op="reports"` once more: `totalCount` must have risen by exactly the number of leads presented. Write the lead id, the campaign and `stage="queued"` back to the people table.

## 9. A person reviews and launches
- A person reads the held leads and launches them. **It is the only step that sends, and the agent never takes it.** Read first the rows where step 5 took a fallback persona, and any person your team might know under a previous employer: the exclusion table only knows companies. A LinkedIn invitation can't be taken back once it's in someone's pending list.
- Replies are read back on every run with `lemlist_lead` `op="list"` and `state="replied"`, and marked with `op="interested"` or `op="not_interested"`. A positive reply moves the row to `replied`, is reported to a person by name, and creates the person in your CRM.

## The daily run
- **Check whether the day is already covered.** A scheduled task has no idempotency key: read the ledger for today's covered date first. `ok` stops the run; `partial` or `failed` resumes it rather than starting over.
- **Source a delta**: a three-day window on step 1 and the 24-hour search on step 2, with every domain already in the people table dropped. Two consecutive days with no new company is an alert in the ledger, not a quiet morning.
- **Write the ledger row before finishing, always**, including on a failure, and only after step 6's completion gate: a ledger row saying `ok` over a non-zero `not_attempted` count is a lie the next run will believe.

## Output
Postings sourced per lane, companies after the merge, excluded by reason (intermediary, exclusion table, already enrolled), disqualified by reason, people named per persona and fallbacks taken, emails by source and status, addresses held on the domain gate, LinkedIn-only people, the completion count (zero), leads loaded per campaign with the review counters before and after, and every hiring title no persona row covers, with its exact wording.