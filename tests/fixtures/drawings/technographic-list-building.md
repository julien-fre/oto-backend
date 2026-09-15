# Find companies that use a specific software tool

**When to use it**: your product only works for companies that already run **[the tool your product plugs into]** (a CRM, a help desk, an ERP), and a rep describes a segment in one sentence. The run turns that sentence into a claimed, enriched and tiered account list and a paused campaign, where the stack is checked on company-level technographic data instead of guessed from a single job ad.

```
              Natural language input in Claude
              "Find 40 companies in the Nordics that run the help desk we integrate with, 50 to 500 staff."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the keys before spending             ║   lemlist_team, hubspot_owners
║  Free reads confirm every key, the campaign     ║   data_rows on the config table
║  team, the CRM owners and your config.          ║   nothing is spent before this
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Build the universe on the stack            │   theirstack_companies_search
│  Your tool and its rivals in one search, kept   │   your slug and rival slugs, one call
│  where enough postings back the detection.      │   billed per company record returned
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ wrong tool        only a rival tool detected
                         ├───────────────▶  ▪ not a user        an agency, or a single posting
                         ├───────────────▶  ▪ unknown stack     a named domain with no record
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Claim before anything is bought            ║   data_rows, data_write on the register
║  The register says who may work each company,   ║   hubspot_object for customers and deals
║  so a held one costs nothing.                   ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ owned             held, excluded or resting
                         ├───────────────▶  ▪ customer          a live account or an open deal
                         ▼  claimed by this run's owner
┌─────────────────────────────────────────────────┐
│  3 · Read why now                               │   theirstack_jobs_search, full=true
│  Hiring and news signals per claimed company,   │   serper_search, news
│  each kept with its exact sentence.             │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not a user        its ads sell the tool to clients
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Find who to write to                       │   apollo_search_people, apollo_match_person
│  At most two people per company, matched to the │   fullenrich_enrich_linkedin as fallback
│  buyer the signal points at.                    │   lemlist_lead, hubspot_object for dedup
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ unreachable       no email and no profile
                         ▼  a reachable person exists
┌─────────────────────────────────────────────────┐
│  5 · Score, tier and write the copy             │   no provider is called here
│  Tier each company, then one email per person   │   copy in the prospect's language
│  with only citable figures.                     │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no draft          no citable claim fits
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Push to the CRM and stage the send         │   hubspot_object, one note
│  One note per company, every lead in a paused   │   lemlist_campaign, lemlist_create_lead
│  campaign a person launches.                    │   never a start or launch call
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Record every company and report            │   data_write, slack_post_message
│  One row per company and per person, then a     │
│  recap of what was skipped and spent.           │
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

## Rule zero: the stack qualifies the company, a single job ad only times it
Building a universe from job postings looks cheap and right: an ad asking for "experience with Tool A or Tool B" seems to name the tool a company runs. **An ad naming two tools names neither.** Searching job boards *for* the tool name makes it worse. Consultancies and agencies write it into their ads because it is their practice area, while the companies that actually run the tool are hiring for something else.

Technographic data does not escape this by magic: it is built from those same postings. What it adds is aggregation. A detection is a company-level fact, accumulated across all of a company's postings over time, so it can be required to rest on more than one ad, and the companies that sell the tool as a service can be filtered out on industry before anyone is claimed (step 1). A single ad keeps the one job it is good at: timing (step 3). A company is never promoted on the strength of one ad, a profile bio or a careers page.

## 0. Check the keys before spending
- <tool:lemlist_team> — `op="team"`, free. Confirms the sending tool's key and the team the campaign will live on.
- <tool:hubspot_owners> — the rep roster as the CRM knows it. The owner of this wave must be one of them.
- <tool:data_rows> — read your config table by row name: **[max people per company]**, **[claim window in days]**, **[cool-off after a touch]**, **[minimum postings behind a detection]**, **[signal window in days]**, your tool's technology slug and the rival slugs in its category, the CRM pipeline and the stage ids that mean open, won and lost, and the report channel id. A missing row blocks the run and is named, exactly like a missing key. It is never replaced by a default, because a default that looks like a decision is worse than a stop.
- Parse the rep's sentence into an explicit brief and echo it back before spending: geography, headcount band, industry, target count, copy language, any named accounts, and the rep who owns the results. **Refuse to guess the owner**: an unattributed wave cannot be claimed.
- A missing key closes the run as blocked instead of degrading into a partial wave. Half a segment claimed and never worked is the worst state the register can be in.

## 1. Build the universe on the stack
- <tool:theirstack_companies_search> — **one search for the whole category**: `extra={"company_technology_slug_or": ["<your slug>", "<rival slug>", …], "min_employee_count": …, "max_employee_count": …, "include_total_results": true}` with `company_country_code_or` from the brief, a bounded `limit` and `page`, and `full=true` for the raw records. **Billing is per company record returned, so `limit` is the spend bound.** Confirm every slug first on a one-record call: slugs are not brand names, and a fuzzy guess returns a different technology with full confidence. `metadata.truncated_companies` above zero means the credit balance cut the list, not your filter.
- **Sort every record on what was detected.** Your tool present: in, with any rival detected next to it noted on the row (a company running both may be mid-migration). **Wrong tool**: only a rival is present. Record which one. Over a few waves that list becomes your evidence for which integration would open the most market. Searching your slug alone can never produce this exit, which is why the rivals ride in the same call.
- **Not a user**: the aggregation still lets two kinds of false positive through, and both are filtered here before a claim is written. A record whose `industry` is an agency, consultancy or IT-services label is tagged with the tool because it sells work on it, so it is dropped. And a detection resting on fewer than **[minimum postings behind a detection]** postings in the raw `technologies_found` entry is one ad again, only stored at company level, so it is dropped too.
- **Unknown stack**: a domain that enters from outside the search, named in the rep's brief or already sitting in the register, is checked on the same call shape with `extra={"company_domain_or": [<those domains>]}`. No record back means no technographic evidence at all. It is counted, reported and left out of the wave. It is not a rejection, and it is not promoted on any other evidence.
- The raw record is heavy on larger companies, so keep only what the row needs: domain, name, headcount, industry, and the detections in your tool's category. Those detections are what make the exits checkable later.
- Stop at the target count, and report the total the search returned so the rep can see whether the segment is bigger than the wave.

## 2. Claim before anything is bought
- <tool:data_rows> — read your target register for the whole universe in one call, `filter={"domain": {"in": [<normalised domains>]}}`, instead of one lookup per company. Each domain comes out as workable by this owner or with a named refusal: `owned` (another rep holds a claim inside the claim window), `excluded` (on your exclusion list) or `resting` (touched inside the cool-off), all three stopping the row at the same point, or `customer` from the CRM reads below.
- <tool:hubspot_object> — `op="search"` on `companies` with `filters=[{"propertyName": "domain", "operator": "IN", "values": [<up to 100 normalised domains>]}]` and `properties=["domain", "lifecyclestage"]`, never the display name. `lifecyclestage` at `customer` is a live account and ends the row.
- <tool:hubspot_object> — `op="get"` on each remaining company with `associations=["deals"]`, which returns the deal ids inline instead of one associations call per company, then `op="get"` on `deals` with `properties=["dealstage", "pipeline"]` for each id. A deal whose `pipeline` is your sales pipeline and whose `dealstage` is one of the open stage ids from the config table ends the row as `customer`. Compare ids, never stage labels, which get renamed.
- <tool:data_write> — the claim row: domain, owner, `source_signal: "technographic"`, claim date, and what this wave learned (name, country, headcount, tools detected). The register accumulates what every wave found instead of each wave keeping it privately.
- **Take the answer as final.** There is no override. **No billable per-company call is made for a company this run has not claimed**: no jobs read, no people search, no enrichment. That order is what lets two reps work the same market without paying twice or writing to the same person.

## 3. Read why now
- <tool:theirstack_jobs_search> — `extra={"company_domain_or": ["<one claimed domain>"]}`, `posted_at_max_age_days` set to **[your signal window, e.g. 90]**, **`full=true`**. The default projection carries no job description, and the sentence worth quoting lives in the description; without `full=true` every signal fails the quote rule below while the run still looks successful. An empty result is a normal answer: the company published nothing, which is not the same as never having been indexed.
- <tool:serper_search> — `kind="news"`, `query="<company name> funding OR hiring OR appointment"`, `tbs="qdr:m"`, `num=5`, with `country` and `language` from the brief. Both default to French, so a segment in another market silently reads the wrong edition of the news without them.
- **Store the verbatim sentence with every signal.** An urgency claim with no quote behind it cannot be checked by the person reviewing the draft, so it is not written.
- Filter the false positives you know about explicitly. Your signal keyword used in a different sense (the same word in a finance or legal posting often means something else entirely) drops that signal and nothing more. Phrasing about work done "for our clients" is different: it marks an agency the industry filter in step 1 missed, and **it is the only thing in this step that stops a company**. The row lands as `not a user` and its claim is released before any people search is paid for.
- A signal raises a tier and gives the opener a reason, and it tells step 4 which buyer to pick (a company hiring its first sales manager is a message to the founder). Its absence lowers the tier and nothing more: **a missing signal never gates.**

## 4. Find who to write to
- <tool:apollo_search_people> — `domains=[<domain>]`, `titles=[<your buyer titles>]`, `seniorities`, `person_locations=[<country>]` and `per_page=3`. A domain is worldwide, so without the person's location a subsidiary of an international group returns people in every country. Take at most three candidates and keep **[max people per company]**; two is a sensible start. The second person exists so the wave survives one of them being away. A third costs a full enrichment record and has to earn its place in your own reply data first.
- <tool:apollo_match_person> — reveal a picked person with `person_id`, the `id` from the search result. Search results obfuscate last names, and a first name plus company matches nobody while still charging a credit. A response carrying `person._stub: true` is a failure, not data.
- <tool:fullenrich_enrich_linkedin> — only for picked people the Apollo reveal returned no address for, as **one job for the whole wave** (up to 100 contacts), each with `linkedin_slug` (the slug, not the URL) or `domain`, and `enrich_fields=["contact.work_emails"]`, since phones cost ten times more. The job is asynchronous: collect it with <tool:fullenrich_result> after about 30 seconds, then every 20 to 30 seconds. On a daily credit ceiling, split the wave across days and say so instead of silently truncating it.
- **Enrich only people you are going to draft.** A candidate considered and not picked is never enriched.
- **Unreachable**: nobody at the company resolves to an email or a profile. The company keeps its claim (it is a real target that could not be reached today) and is reported so a person can find someone by hand.
- **Deduplicate before drafting, by email, against both systems.** <tool:lemlist_lead> with `op="get"` and `email` is the workspace-wide lookup. `lemlist_get_leads` lists one campaign's leads and cannot answer "is this address anywhere", so a dedup built on it silently passes everyone. A person already in a sequence stops.
- <tool:hubspot_object> — `op="search"` on `contacts` with `filters=[{"propertyName": "email", "operator": "IN", "values": [<the wave's emails, up to 100>]}]`, then `op="get"` on each match with `associations=["deals"]` and the same `dealstage` and `pipeline` read as step 2. A person who is a CRM contact on an open deal stops, and the deal owner is named in the recap.

## 5. Score, tier and write the copy
| Tier | What it means |
| --- | --- |
| A | Tool detected, inside the headcount band, and a hiring or news signal inside your signal window |
| B | Tool detected and inside the band, no signal |
| C | Tool detected but at the edge of the band, or the signal contradicts the fit |

These tiers are reasoned, not fitted. Re-derive them from reply rates per tier once a wave has come back.

Then one email (and one short connection note, if you use one) per person, **in the prospect's language**. The opener is the signal where there is one and the segment where there is not. If you sell several use cases, pick the one the signal points at, and only that one.

Two mechanical checks run before a draft is written to its row:
- **Every number in the body appears in your proof-points table, in that exact form, marked citable.** A claim with no row is not made, and a figure with no source is not printed however good it sounds.
- **No customer is named** unless the proof-points table says that name may be cited. Nothing on your roadmap is described as shipped.

A draft that fails either check is not written. The row lands as `no draft` with the reason, which is a finding about your proof points, not a failure of the run. Write plain text into the variables: sequence copy is HTML, so an unescaped ampersand or angle bracket breaks the markup around it.

## 6. Push to the CRM and stage the send
- <tool:hubspot_object> — `op="add_note"` on the company record: tools detected, the signal with its quote, the tier and the drafted angle. **Nothing else**: no property, stage, deal, owner or contact. Everything this run writes to the CRM can be undone by hand in a minute.
- <tool:lemlist_campaign> — **check the lock before a single lead goes in.** A campaign with auto-review on turns `lemlist_create_lead` itself into a send. Reuse the lane's standing campaign only when `op="reports"` on it shows `state` paused and the review lock holding: `reviewedCount` and `inSequenceLeadCount` at 0. Otherwise `op="duplicate"` a template, which is born paused. A fresh `op="create"` is already running even though it displays as draft, so chain `op="pause"` before adding a lead. Name the campaign for the segment and the date so a dashboard can group waves. Read the sequence shape your team already uses with <tool:lemlist_sequence> instead of inventing one, or waves stop being comparable.
- <tool:lemlist_create_lead> — one lead per person with every personalisation value in `custom_variables` in the same call, rather than patched afterwards one variable at a time.
- <tool:lemlist_campaign> — `op="reports"` again after loading: `totalCount` up by the wave, with `reviewedCount`, `inSequenceLeadCount` and `emailsSent` unchanged, proves nothing is queued to send, without sending anything. **No start or launch tool is ever called.** A person reads the campaign and launches it.

## 7. Record every company and report
- <tool:data_write> — one batch per table. One row per company, **including every excluded and stopped one with its reason**, and one row per person keyed `domain:lastname-firstname`. If several sourcing lanes share the person table, write the lane that found this person on every new row and **never rewrite it on a row that already exists**: a person another lane met first keeps that lane, or reply rates per signal quietly move from one lane's count to the other's. An upsert merges, so omit a field rather than blank it: a field written empty destroys what an earlier run worked out.
- <tool:slack_post_message> — one recap to your team channel. The bot must be a member: `not_in_channel` is a failed post, not a quiet run.

```text
Technographic wave · <segment> · <owner>
Universe <n> of <total> · claimed <n> · drafted <n> people at <n> companies · campaign <name>, paused
Tier A <n> · B <n> · C <n>
Skipped: <n> wrong tool · <n> not a user · <n> unknown stack · <n> owned by others · <n> customers or open deals · <n> unreachable · <n> no draft
Spent: TheirStack <n> records · Apollo <n> reveals · FullEnrich <n>
```

**The skipped line is not padding.** It is how a rep finds out that half the segment already belongs to someone else, or that the stack filter returns almost nothing, before the next wave repeats it.

## Output
The brief as it was echoed back; the universe size against the total the search reported; the wrong-tool list with the rival detected; companies claimed, and every refusal by reason; tiers; people drafted and the dedup stops; the campaign name and its paused, unreviewed state from the reports call; credits spent per provider; and one line naming any key, config row or ceiling that cut the wave short.