# Source, enrich and sequence prospects on email and LinkedIn

**When to use it**: you sell to companies, can write your ideal customer in four or five lines, and want one run to go from that brief to staged email and LinkedIn touches, with only the conversations that turn real landing in your CRM.

```
              Natural language input in Claude
              "Logistics companies in the Nordics, 100 to 1,000 staff. Source 50 accounts."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Check the tools before spending            ║   lemlist_team, lemlist_mailbox
║  Free reads confirm the senders, the campaign   ║   lemlist_campaign, lemlist_sequence
║  variables and the CRM key.                     ║   hubspot_object
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Source and score the accounts              │   apollo_search_organizations
│  Stack two sourcing layers, then score each     │   theirstack_companies_search
│  account on firmographics alone.                │   apollo_bulk_enrich_organizations
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ parked            excluded, or scored tier C
                         ▼  tier A or B only
┌─────────────────────────────────────────────────┐
│  3 · Choose the buying committee                │   apollo_search_people
│  Take the people who run the process first,     │   one domain per call
│  then the one who owns the budget.              │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no contact        no signals are bought
                         ▼  a contact exists for this company
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  4 · Buy signals per company     │  │  5 · Find email and phone        │   apollo_match_person
│  Jobs and stack from TheirStack, │  │  Reveal by id, fill gaps down a  │   fullenrich_enrich_linkedin
│  news and ownership from Serper. │  │  waterfall, drop false matches.  │   kaspr_enrich_linkedin
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     ├──────────▶   ▪ unreachable   no email, no phone, no LinkedIn
                 └───────┬─────────────────────────────┘
                         ▼  company scored and person reachable
┌─────────────────────────────────────────────────┐
│  6 · Write one opener per person                │   one dated signal, or a role hook
│  Rank the hook, match the seat's message, and   │
│  write it in that person's own language.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Stage the email and send the invite        │──▶  Lemlist campaign  matched on seat and language
│  Add the lead with its opener as a variable,    │──▶  LinkedIn invite  paced per sending identity
│  held for review, then invite on LinkedIn.      │   lemlist_create_lead, linkedin_unipile_network
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Calling                                    │   a human works the phone
│  A person works the list by phone and records   │
│  the outcome in their own words.                │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not synced        no meeting, callback or reply
                         ▼  a meeting, a callback or a positive reply
┌─────────────────────────────────────────────────┐
│  9 · Write real leads to HubSpot                │──▶  HubSpot  the company, then the person, then a note
│  Sweep the replies, then write out only the     │   lemlist_get_activities, linkedin_unipile_chat
│  rows that qualify.                             │   hubspot_object
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

**The spend rule.** Each step gates the next: sourcing is almost free, firmographic enrichment is cheap, people search costs, signals and contact reveals cost most. Nothing expensive is bought for a company that already failed a cheaper test. Steps 4 and 5 run in parallel; step 6 needs both an account at `scored` and its lead at `enriched`.

**Two tables, one status column each.** Accounts are keyed on `domain`, leads on `<domain>:<person id>`, and every batch write passes the key so a re-run upserts instead of duplicating. Scoring onward, except the calling step, is a claim loop: <tool:data_claim_next>, work the row, <tool:data_write>, then <tool:data_release> after **every** row, terminal or not. Writing a final status frees nothing, and a skipped release makes the queue look drained, then refill when the lease expires. <tool:run_finish> releases whatever a dead worker still held. Every step reads a status and writes the next one, so a run can resume from any step.

## 1. Check the tools before spending
Free reads, no credits, and they decide whether this is a sending run or a research run. Report all of them before sourcing: a ranked account base with an opener per lead is still a real deliverable, but the operator should know up front that it is the only one.
- <tool:lemlist_team> op=user_channels for the mailboxes, then <tool:lemlist_mailbox> op=test on each one attached to the campaign you will use. **Test the mailbox, don't read the label**: a sender whose provider token expired still shows as attached and configured, nothing sends, nothing refreshes, and the campaign keeps saying running while every send fails.
- <tool:lemlist_campaign> op=statutes on that campaign runs the same validation as the Lemlist interface: level 3 blocks (no sender, broken DNS), level 2 warns (daily limit, no schedule). Read recent bounce counters with op=batch_stats. Above **[5%]** bounces, or a mailbox still warming up, and dispatch does not run until the domain recovers.
- <tool:lemlist_list_campaigns> for the live register. Never trust a written list of campaign ids, and read `truncated` before concluding a campaign is missing. **Check that a campaign exists for every seat and language this run will produce** before sourcing: finding out at step 7 that a persona has nowhere to go means every credit spent on it bought a call card, not an email.
- <tool:lemlist_sequence> op=get to read the copy and **write down every variable it references.** Step 7 must send a value for each one; a lead missing a variable is held back silently rather than sent. The variable names are the contract between step 6 and the campaign, and they cannot be inferred.
- <tool:hubspot_object> op=search on companies with a limit of 1 proves the CRM key answers. A read-only key passes this and fails every write in step 9, and no tool here can read a key's scopes, so a person confirms in HubSpot that the key can write companies, contacts and notes before the run.
- **A sender coming back from an outage has a backlog.** <tool:lemlist_lead> op=list with state=paused on its campaigns shows what is queued behind it, and reconnecting releases all of it at once. Decide what should still go before reconnecting.

## 2. Source and score the accounts
The brief is typed in chat in four or five lines: **[segment]**, **[region]**, **[size band]**, **[volume]**, optionally the use case to lead with. Or paste a list of domains and start at scoring.

Two sourcing layers, stacked, then deduplicated on the domain:
- <tool:apollo_search_organizations> with `employee_ranges` from the brief. One credit per page of 100, so a wide net costs the same as a narrow one. **Use a region, not a city, and split the category into several single words**: a city plus one long keyword phrase returns almost nothing, where the same search as three single words returns a real universe. Pass `fields` while paginating, because a full page is past most clients' output cap. Keyword tags are loose, so the result will not be the segment you asked for: filter on industry code and the company's own description before writing rows.
- <tool:theirstack_companies_search> for reverse discovery from hiring: companies with open roles in the function your product relieves (`job_filters` and `min_num_jobs_found` in `extra`). A company hiring for the pain has admitted the pain grew. Where your vertical has an official public register, it beats any sampled database as a universe, because it is exhaustive.

**Hard exclusions on the way in**, status `excluded` with a reason: **[your disqualifier list]**, competitors, B2C or marketplaces with no sales motion, and **anything already in the CRM**: <tool:hubspot_object> op=search on companies, and on deals for an open opportunity. A customer or a live deal re-entering a cold sequence is the worst failure this process can produce. An exclusion that needs judgment, such as a company that fits both an excluded category and your target one, gets its reasoning written down and a flag for a human, never a silent call either way.

**Score before buying anything.** <tool:apollo_bulk_enrich_organizations>, 10 domains per call (the API refuses more; a batch saves calls, not credits) for headcount, department split, founded year, ownership and funding. Score out of 100 on four or five fixed sub-scales, for example **[business-model fit]**, **[size band]**, **[decision access: independent or subsidiary]** and **[a proxy for the pain]**, with the points for each band written into the procedure. Without written sub-scales two workers score the same company differently. Cutoffs are yours to set; a starting grid is Tier A at **[70]** and up, B at **[50 to 69]**, C below that. Tier C is parked, not excluded: nothing more is spent on it.
- **Never score from the database revenue figure.** At mid-market size it is routinely off by an order of magnitude. Keep it as a note, never an input.
- **Ownership fields go stale.** Score them now; step 4 re-tests them.
- Geography is a weight inside fit, not a gate, unless your brief says otherwise.

## 3. Choose the buying committee
Tier A and B only. <tool:apollo_search_people> **one domain per call**: five domains in one call return a single relevance-ranked page where one company crowds out the rest, and coverage per account becomes luck.
- **Two seats, searched in order.** Up to **[two]** people who run the process day to day, then **[one]** who owns the budget. Take the user seat first: it cannot buy, but it can describe the problem without asking permission, and that description is what makes the budget conversation land. On a committee sale, widen to **[up to five]** people across the functions that sign.
- **Always pass `person_locations`.** A domain is worldwide: on a group with foreign sites, a domain-only search surfaces people in the wrong country, and each reveal costs a credit.
- **A title search that returns nobody was too narrow; it does not prove nobody is there.** Before writing `no_contact`, search the leadership titles directly, then seniority with no title filter, then read the company's own team page.
- **Franchise and dealer networks break the budget seat**: owner and president titles return franchisees. Drop those titles, search finance and operations leadership, and expect the real seat at the parent company, on the parent's domain.
- **Last names come back masked**, so the lead key is the domain plus the person id, never a name. Store the seat and the persona the search title suggests, and **mark the persona provisional**: the title a search matched is often not the person's real job.
- Nobody found means the account goes to `no_contact`, terminal. **No signals are bought for a company nobody can be reached at.**

## 4. Buy signals per company
Only for accounts with at least one selected lead. Signals are bought once, on the account row, never per contact.
- <tool:theirstack_companies_search> on the domain for the stack. **An incumbent tool in your category is a negative signal.** Expect three things: duplicate records for one domain with different headcounts (take the union of the stacks and the larger headcount), no record at all for many mid-size firms (a normal result, don't retry), and whole categories missed, so an absent layer is weak evidence. Apollo's own technology list is a usable second opinion.
- <tool:theirstack_jobs_search> on the employer, with `posted_at_max_age_days` set and `limit` as the cost ceiling. Open roles in the function you relieve are the strongest single signal this step can find.
- <tool:serper_search> with kind=news for funding, expansion and leadership changes, and <tool:serper_scrape> on the about or leadership page when the site is thin.
- **The independence re-test can kill an account.** Search explicitly for the company being acquired, merged or absorbed. If it has been, the decision-access points from step 2 are wrong and no signal bonus undoes that: status `excluded` with the source URL, and stop. This is the only step that can see it.

Apply the layer to the base score, capped at **[plus or minus 15]**. A starting grid, to tune to your market: **[+5]** relevant roles open; **[+5]** a stack gap your product fills, **only where the stack was actually identified** (an unreadable stack scores 0, not the bonus); **[+3]** expansion or M&A in the last six months; **[+3]** a leadership change in the buying function in the last twelve months; **[−10]** an incumbent in your category; **[−10]** **[above your size ceiling]**. **Signals decay**: for example, older than about **[six months]** counts half, older than **[eighteen months]** counts zero.

Every finding is a row in the account's signal list with `source`, `type`, `detail`, `url` and `captured_at`. The URL is what lets the caller check a claim before repeating it. Nothing found means score equals base score and "none found" is written. An account with no signal is allowed; an invented one is not.

## 5. Find email and phone
Leads at `selected`, in parallel with step 4. A fixed provider order, each dearer than the last.
- <tool:apollo_match_person> with `person_id` from the search. **Never first name plus company**: that matches nobody, and Apollo mints an empty record and charges the credit anyway (the answer carries a stub flag; treat it as a failure). **Read the contact layer, not just the person layer**: the person email can say verified while the contact record in the same response says the address was invalidated by a hard bounce. An address is usable only when both layers agree, and Lemlist will not catch it, because it sends whatever it is handed. Responses are large, so delegate a batch to a sub-agent that returns only the fields the table needs.
- <tool:fullenrich_enrich_linkedin> on email misses where a LinkedIn slug is known: **up to 100 contacts in one job**, not parallel single calls, with `enrich_fields` limited to work emails when you only intend to email, since a phone costs about ten times an email. Collect with <tool:fullenrich_result> after about 30 seconds, then every 20 to 30 seconds until finished. **It is not an independent check**: compare what it returns with the address Apollo already rejected. A fallback that hands back the bounced address is not a second source.
- <tool:kaspr_enrich_linkedin> for the mobile, one call per person, `with_phone` on, and field names from Kaspr's own list (an unknown name answers 500). Apollo's direct-phone flag promises more than it delivers: a number typed as work headquarters is the switchboard, identical for everyone at the company. Keep it if it is all there is, and say so in the call notes.
- **Re-map the persona on the revealed title.** This is the first moment the real job is known, and a real share of provisional tags will be wrong. The persona picks the campaign in step 7, so a wrong tag is a wrong message, not a cosmetic error. Where the right persona does not exist in your list, keep the old one, note the correction, and raise it.
- **Purge false affiliations.** Three checks on every enriched lead: the email domain belongs to the account, or a reason is recorded on the row (a group officer who holds the same role at two entities is real, and the employment history says so); the employer the provider returned matches the account; and the same person is not attached to several accounts in this run. That last one is the signature of a provider falling back to name matching and extrapolating an email pattern across domains, and one bad fallback poisons many accounts at once. A lead that fails goes to `unreachable` with the reason, and the purge count goes in the report: it tells the operator whether sourcing was clean.
- **Watch the balances you can read, and treat a silent batch as an empty one.** On the shared platform key, each <tool:apollo_match_person> response carries `platform_quota.remaining`: stop the batch before it reaches 0, because the next call fails outright. FullEnrich and Kaspr have no balance read here, so a whole batch that comes back empty is a possibly exhausted balance: stop and check it, never mark every lead in it unreachable. Enrichment failure is never fatal to the run: the row stops, the run continues. No email, no phone and no LinkedIn means `unreachable`. A good name and title with a LinkedIn profile is still a lead for the invite and the call.

## 6. Write one opener per person
Leads at `enriched` whose account is `scored`.
- **Read the campaign copy first** with <tool:lemlist_sequence> op=get. The opener is dropped into a body someone else wrote, so write it to hand over cleanly to the paragraph that follows.
- **Rank the hook.** A personal signal (a recent appointment, a press mention of the person) beats an account signal dated within **[120 days]**, which beats a role-based hook, used only when both are empty. Never a signal you cannot verify, never one older than eighteen months. Record which kind was used, and **report the split before step 7**: a batch that is mostly role hooks means step 4 did not do its job, and re-running it beats sending.
- **The seat changes the message, not just the tone.** To the user seat: the mechanism in their own vocabulary, a named case, and a question about how they handle it today. **No figure, no ROI, no headcount framing**, because the saving being described is their own job. To the budget seat: the structural argument, with **[your one verified customer result]** or no figure at all.
- **The figures gate is absolute.** A figure goes in only if your own register marks it verified. Frequency labels in a case library are judgments, not measured rates, and third-party benchmarks are not your findings. A figure the prospect's own press published about the prospect is an observation, and allowed.
- **Some true signals are not openers.** Bad news about the company, or a named incumbent vendor, reads as an attack in a first message. Mark it as second-conversation material for the caller.
- **Language is decided per person, not per account.** In order: the language of their own headline and title as they write it, where they live, their career. The company's country, domain suffix and website language are context, never the answer. One account with two languages is normal in multilingual countries.
- **Two fields, never one.** `outreach_angle` (the wedge, the opener, one line on why it lands) is for the caller; `opener` alone goes to the campaign. Writing the whole block into the sent field mails a third-person rationale to a stranger.
- Register: complete sentences, **[80 to 90]** words for a budget seat and shorter for a user seat, plain text, no links, no signature, no flattery. Lemlist copy is HTML: newlines inside a variable collapse, and an ampersand or angle bracket breaks the markup around it, so write plain prose without quotation marks.

## 7. Stage the email and send the invite
Leads at `angle_ready`. Four guards before adding anyone:
1. The email is usable under step 5's both-layers rule. If not, the lead is LinkedIn and phone only.
2. The lead is not live in another campaign: <tool:lemlist_lead> op=get with the email. A hit is a sequence already running from your domain.
3. The lead is not on a do-not-contact list: <tool:lemlist_unsubscribe> op=get for emails and whole domains, op=var_get for a LinkedIn URL. These lists are separate, and writing one does not write the others. **A hit is mirrored onto the lead row** as an exclusion with its reason, so later runs skip it: otherwise every run re-stages a lead Lemlist silently skips, and reports it as contacted.
4. Every variable noted in step 1 has a value on this lead.

- **Match the campaign on seat and language.** Naming is load-bearing (**[segment] / [seat] / [language]**) or the lookup will not find it. **No campaign for that pair means the lead waits**: leave it at `angle_ready` and name the missing campaign in the report, never force it into the nearest one.
- <tool:lemlist_create_lead> with the campaign id, email, names, company, and the opener in `custom_variables`, with `deduplicate` on so the insert is skipped if the email already sits in another campaign. The opener rides on the lead and resolves at send time, which is why the copy can be edited without touching every lead.
- **The lead lands held for review.** <tool:lemlist_campaign> op=reports proves the state of that lock without sending anything: `totalCount`, `reviewedCount`, `inSequenceLeadCount` and `emailsSent` show the leads are in and nothing has left. No tool here renders an assembled message, so a person reads a sample in Lemlist's own review view, then the leads are released with <tool:lemlist_launch_lead>. That tool is the actual send gesture and is hidden by default: it is enabled on purpose (<tool:oto_enable_tool>) for the release, never left on for the rest of the run. Auto-review restricted to deliverable addresses is a deliberate switch for a standing campaign whose copy is already proven, never a default. A launched lead cannot be corrected, so the review happens before, not after.
- **What Lemlist does without telling you**: the From address and signature belong to the senders attached to the campaign, not to the owner of the API key; daily limits are per sender, so many leads on one mailbox quietly spread over weeks; the sending window's timezone belongs to the campaign, not the lead; and sending stops silently at zero credits while the status still says running. Read the balance with <tool:lemlist_team> op=credits before the release, and watch real send activity, not the status.
- **LinkedIn goes out separately, paced per identity.** <tool:linkedin_unipile_network> op=invite with `provider_id` from a <tool:linkedin_unipile_profile> result (not the profile URL) and a note under 300 characters: one observation, no ask, not the email opener. Pace per sending identity, well under the weekly cap, and back off on a 429, anywhere from seconds to an hour. The invite deliberately does not use the campaign's own LinkedIn steps, so it is paced against the identity's limit rather than the campaign's.
- **Rehearsal mode.** The invite has no dry run, so a rehearsal does not call it. Record the intended touch with an action starting with the word REHEARSAL and leave the lead at `angle_ready`. **A lead marked contacted when nothing was sent is worse than no status at all.**
- Record each touch with its channel, date and campaign, and move the lead to `contacted`.

## 8. Calling
A person works the leads table by phone, reading the angle, the signal summary and the cases to raise, and writes back a call status, a callback date and notes in their own words. Leads whose only number is the switchboard, or whose seat sits outside the target region, say so in the notes so nobody plans the wrong call.

## 9. Write real leads to HubSpot
**Sweep replies first.** Nothing else sets a reply status, so without this only a phone call could ever qualify a lead.
- <tool:lemlist_get_activities> with the campaign and `activity_type`=emailsReplied, then a second call with emailsBounced (one event name per call). <tool:linkedin_unipile_chat> for LinkedIn replies: compare the last message's sender id to your own account, because the "is sender" flag is unreliable. Set the reply status to positive, negative, out of office or referred. **A referral names the seat the process failed to find; hand it to a person immediately.**
- Lemlist stops a lead on reply or bounce by itself but writes nothing to your table. Record every bounce and blank that lead's email source, so no later run sends to it again.

Then sync **only** rows with a booked meeting or a callback, or a positive or referred reply. Everything else stays in the table: a CRM full of cold names is how a pipeline number stops meaning anything. In this order:
1. <tool:hubspot_object> op=search on companies by domain. **Create only when the search comes back empty**; a second record for the same company is worse than no write at all.
2. op=create for the company when needed, then op=create for the contact with its association to the company.
3. op=get on the contact, asking for its company associations. **Read the association back**: two records sharing a domain are not linked.
4. op=add_note on the contact with the angle, the cases raised and the signals with their source URLs. A note cannot be updated and a re-run writes a second one, so store the note id on the row and skip when it is set.

- Store the CRM ids on the row as soon as each write lands, so a retry after a failed contact write updates instead of duplicating. On failure mark the CRM status failed, leave the lead at `contacted`, and report it. Never half-write a record and call it done.
- **Never create a deal.** A booked meeting is not an opportunity; a person opens the deal after the meeting happens.

HubSpot is the CRM here; any CRM with search, create, associations and notes works the same way.

## Output
The preflight verdict (sending run or research run, and why); accounts sourced, excluded with reasons, and counted by tier; accounts with no reachable contact; leads enriched, unreachable, and purged as false affiliations; persona corrections made at reveal; the hook split; leads staged per campaign, leads waiting on a missing seat-and-language campaign, and invites sent; replies by type; and the leads written to the CRM, with their ids.