# Sync Granola call notes into the right Folk pipeline

**When to use it**: your team's calls are written up automatically in Granola, but the CRM only moves when someone remembers to update it by hand, and not every call is a deal. This sweeps every note in the workspace, decides what kind of call each one was, and writes it to the record that kind of call belongs on: the company in the sales pipeline, the candidate, the partner or the investor, with a recap note that doubles as the sync marker.

```
              Scheduled routine, every 12 hours
              "Pull the team's call notes from the last 13 hours and update the CRM."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  Sweep the window, keep the unsynced calls      ║   granola_content
║  Notes created or updated in the window, minus  ║   created_after + updated_after
║  calls the CRM already holds a link to.         ║
╚════════════════════════╤════════════════════════╝
                         ├──────────────▶  ▪ nothing new  every call already synced
                         ▼  each remaining call, oldest first
┌─────────────────────────────────────────────────┐
│  1 · Read the call and classify it              │   granola_content  op=get_note
│  Sales, talent, partner, investor or internal,  │   the summary is the source
│  decided before anything is written.            │
└────────────────────────┬────────────────────────┘
                         ├──────────────▶  ▪ internal     no external thread, or a prep note
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Route and update the record                │   folk_record
│  Sales goes to the company in the pipeline;     │   status forward-only, deal at list price
│  talent, partner and investor to the person.    │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Read back what the CRM did                 ║   folk_record  op=get
║  Every create and field write is re-read: the   ║   a value you never sent is a finding
║  CRM can mint a twin company on its own.        ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write the recap note                       │   folk_record  entity=note
│  On the person the call was with, in its        │   the link is the sync marker
│  language, closing with the link to the call.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Report the run                             │   pass counts and late notes included
│  Every call, its class and outcome, and each    │
│  judgment call made on your behalf.             │
└─────────────────────────────────────────────────┘

▪ terminal — nothing is written to the CRM from there
```

Steps 1 to 4 repeat **once per unsynced call**, oldest first, each call carried to its recap before the next begins. A call that exits as internal does not stop the run. A call can carry two classes at once, a customer sync where the customer also hands over an introduction, and then it routes to both, one record and one note each.

## Sweep the window, keep the unsynced calls
**A note does not exist for the API at the moment of the call.** It appears once the note is generated and shared, sometimes hours later, so the run that owns the call's window can sweep before the note is readable. Sweep **twice** and union the results by note id:
- <tool:granola_content> `op="list_notes"`, `created_after=<now − 13 hours>`: the calls that happened in the window. The window is an hour wider than the cadence, so a boundary call is caught by one run or the other.
- <tool:granola_content> `op="list_notes"`, `updated_after=<now − 13 hours>`, `created_after=<now − 7 days>`: the calls that became readable in the window, whenever they happened. The seven-day floor bounds the cost; a note returned right at the floor means the floor is now load-bearing, so the report says so.
- Page both with `cursor` (`page_size` up to 30) and take every note, **whoever owns it**. Filtering on the operator's own notes means colleagues' calls never reach the CRM.
- **`updated_at` is not "published at".** A bulk metadata touch, a folder move or a re-index can stamp unrelated notes with the same `updated_at` to the millisecond. A wide gap is a reason to check the sync marker, never on its own a reason to rewrite a record.
- **The sync marker is the link, and only the link.** Every recap this process writes ends with the note's web link (its web_url field). Before processing a call, search the notes on each attendee the CRM already holds (<tool:folk_record> `entity="note"`, `filters={"entity_id": "per_…"}`) for that link; if it is there, drop the call. A record whose status, next steps and deal value already reflect this call but carry no note with the link is **unsynced**: someone filled the fields by hand, so write the recap and leave their fields alone.
- **Two notes can be one call.** When two colleagues attend, each gets a note: two ids, two links, two summaries of the same half hour, and the link check reads the second copy as unsynced. Compare the calendar event id (calendar_event_id) with the notes already handled; the same event, attendees and slot are the same call. Append the second link to the existing recap, fold in what only the second summary says, and touch **no field**. When the two copies disagree on a fact, record the divergence in the note and let a person settle it.
- A window that is empty, entirely synced or entirely internal **stops, writes nothing and reports "nothing new"**. That is a success, not a failure. An empty sweep means no notes were readable, not that no calls happened.

## 1. Read the call and classify it
- <tool:granola_content> `op="get_note"`: the summary is the source. Fetch the transcript (`op="get_transcript"`, paginated) only when the summary is missing or useless; `include="transcript"` on a long meeting can come back `TRANSCRIPT_TOO_LARGE`.
- Pull the external attendees with their emails, minus your own domain, your team's personal addresses and notetaker bots; what the call produced, what was promised, what blocks the next step, any price named, and the language spoken.
- **Take names from the email domain, not the transcript.** Summaries mangle product and company names; never copy one into the CRM without recognizing it first.
- **Date the call from the note's `created_at`**, and take follow-up dates from the summary's own next steps. The calendar event attached to a note can point at a different occurrence than the call it summarizes.
- **Not everything in a summary belongs in a CRM.** Transcription captures the small talk too. Someone's health, family or personal money is not pipeline context: drop it entirely, not paraphrased, and keep only what the business thread needs ("back at work, follow-up late in the month"). Your own internal numbers (revenue, pay) stay out as well. The exception that is not one: a figure that *is* the negotiation, such as the revenue level a counterpart needs for a deal to work, stays in, framed as the constraint it is.

Then decide the class, **before any write**, and report it for every call:

| Class | What it looks like | Where it lands |
|---|---|---|
| **Sales** | a prospect or customer, and a conversation about buying or onboarding: demo, pricing, onboarding, pipeline movement | the **company** in the sales pipeline group |
| **Talent** | hiring: a candidate met or discussed, a role scoped, a recruiter putting profiles forward | the **candidate** in the talent group |
| **Partner** | a channel, reseller, referral or intro relationship, someone who brings deals or people rather than buying | the **person** in the partner group |
| **Investor** | a fund, angel or scout, and a conversation about raising money from them | the **person** in the investor group |
| **Internal** | a team sync, a one-on-one, a solo note, no external thread | nothing: reported and dropped |

- **When ambiguous, prefer the class the next step belongs to**: an intro being made is talent or partner, a payment link being sent is sales, a deck going to a fund is investor.
- **The other side selling to you is not a sales call.** It is a partner or vendor thread, and inventing a deal for it corrupts the pipeline. A peer-founder exchange where neither pitches is partner too. **An investor is not a partner**: filing a fund under partners empties that word.
- **A title, an attendee list or a folder is not a classification.** An external can sit on the invitation, in `attendees` and in a customer-calls folder while the summary is two colleagues talking about that company. The tell is grammatical: nobody discusses a counterpart in the third person with them in the room. Read the summary before the metadata, and flag a close call for a person to confirm. The reverse holds too: an agenda mostly about your own strategy is not internal when the external in the room is the one it is being negotiated with.
- **A prep note is not a call.** A solo note rehearsing an upcoming meeting creates no record. Neither does a fund or company merely named as an option: a record is created when a call **happens** with them.

## 2. Route and update the record
**Sales: the company in the pipeline.**
- <tool:folk_record> `entity="person"`, search by email first. folk's `like` filter is anchored to the start of the string, so a zero count on a multi-word company name is not absence: confirm on the domain before creating anything, or you create the duplicate you were looking for.
- **A matching company exists**: create the person with an explicit `company_id`. Attendee email domains and company URLs drift apart, and letting the CRM resolve a slightly different domain mints a second company.
- **No company exists**: create it by hand first (`op="create"` on a company accepts only `name`, `emails` and `industry`; the URL and description follow in an `op="update"`), add it to the pipeline group, then create the person with its `company_id`.
- **The call never names a company** (a personal, alumni or school address and no stated employer): create no company. A company minted from a personal-mail domain is a permanent duplicate the CRM will offer for every other contact on that domain. Put the person in the pipeline group, put the missing context in their description, and leave the pipeline fields empty; they live on the company. <tool:folk_group> `op="custom_fields"`, `entity_type="person"` shows what the pipeline defines on a person; when it is nothing, there is nowhere to write them, and inventing a company to get somewhere is the one wrong fix.
- **Two field vocabularies.** `op="create"` takes snake_case (`first_name`, `last_name`, `emails`, `company_id`, `group_ids`, `job_title`, `description`); `op="update"` takes camelCase and nests custom fields under `customFieldValues`, keyed by group id. A record must be **in the group before its fields are written**, or the write is refused: use `group_ids` on create, or `add_to_groups` on update.
- Several externals from one company attach to the one company; the person driving the evaluation takes the recap.

**The pipeline fields**, on the company:
- **Status moves forward only, and closed stays closed.** Use your pipeline's own ladder (for example **[Meeting booked → Follow-up pre-demo → Follow-up post-demo → Qualified/Negotiating → Closed Won | Closed Lost]**), read with `folk_group` rather than assumed. The run may set an empty status and advance one the call plainly supports. It never moves one backwards and never sets or touches a closed state: a person closed it knowing something the summary does not.
- **One rung is the habit, not the ceiling.** Advance two only when the call closed two stages at once (the booked meeting *was* the demo) and stopping short would state something false; flag it as a judgment call. The last open rung is the run's ceiling: on a record already there, a follow-up call is a next-steps rewrite and a note, and "no transition available" is the correct outcome.
- **A field a person set from this same call is not the run's to correct.** They had the call too, plus whatever is not in the summary. Write the recap, report the transition declined and why.
- **Next steps is rewritten every sync**: what happens next and who owes it, from the call's own next steps. Stale next steps are worse than none. A closed record's status is frozen, its next steps are not.
- **Deal value is the undiscounted list price of the offer actually in negotiation.** A discount goes in next steps and the note, never in the figure. A bigger tier merely mentioned does not raise it; no price named leaves it empty however far the status moved; a price band with no configuration chosen is not a price, so it stays empty with the band written in next steps; for services, price the configuration proposed (the rate times the shape on the table) and say which one.
- Company size and vertical are set only when the call makes them certain. A wrong vertical is worse than a blank one.

**Talent: the candidate.** The record is the candidate, not necessarily the person on the call: a recruiter's call produces a candidate card for the profile they put forward, and the recruiter goes to partners. A candidate status (for example **[To meet → In progress → Out]**) moves forward only, and the run never sets the closing state. A bare first-name card is a merge risk: enrich it when a later call gives the full identity, and report which card you matched and on what evidence. A customer's champion changing jobs is not a candidate, and neither is a cofounder or equity negotiation; that is settled by people.

**Partner: the person.** Add them to the partner group and write only the fields that group actually carries, read with `folk_group` `op="custom_fields"`; a select with no options defined is left blank. A partner needs no company. An existing customer who brokers an intro **gains** the partner group through `add_to_groups`, keeping their pipeline membership. A prospect who *could* resell you is not a partner until someone raises it; say in the note that it was considered. A long-running partner thread keeps its current state in the description, one dated block per real shift.

**Investor: the person.** Add them to the investor group, with the fund's stage, ticket, focus and what they asked for in the description, and everything about the round in the note. The investor status is forward-only, never closed, and **left blank on a first meeting**: a warm "send me the deck" and a polite brush-off produce the same next step, and a rung guessed high can never be walked back.

## 3. Read back what the CRM did
- <tool:folk_record> `op="get"` on the person after **every** create, and read its `companies`. **Passing `company_id` does not stop the CRM from resolving the email domain on its own**: it can mint a twin company in the same call, empty, already joined to the pipeline, with an AI field filled in. Detach it with `op="update"`, `fields={"companies": [{"id": "com_…"}]}` holding only the company you meant, then delete the twin.
- **A resolved company with the right entity but a mangled name is renamed in place**, never deleted: deleting a right answer only mints another duplicate.
- **Re-read after every custom-field write**, and compare against the read taken *before* writing. The CRM's AI fields can populate a value you never sent on an ordinary update; clear what appeared, and leave alone what was already there.
- **A 422 on a field you never sent**: a single-field update can fail on an owner field the merge resends with both an id and an email. Read the error's `path`, and resend that field yourself as `[{"id": "usr_…"}]` beside the one you meant to write.

## 4. Write the recap note
- <tool:folk_record> `entity="note"`, `op="create"`, `item={"entity_id": <the person>, "content": …}`, on the person the call was actually with. Structure it by what the call produced: context, what happened, decisions, objections, next steps with owners. Name the seller or interviewer when it was not the operator.
- **Write it in the language of the call.** Close with the note's web link: that link is the sync marker the sweep depends on, so it is never optional.
- One note per call per record. A call routed to two records gets a note on each, both ending with the same link, each written for its own reader. A call recorded by two owners gets **one** note carrying **both** links.
- **Put declined changes in the note, not only in the report**: a status left alone, a candidacy left open, a blank investor rung, a deal value left empty although a price was heard, a company-less prospect. The report scrolls away; the note is what the next person opening the record reads.

## 5. Report the run
Every call in the window with its class and its outcome: routed, already synced, second copy folded in, or internal. For each routed call, the records touched, the status transition made or declined, and every judgment call taken on your behalf. The sweep's own evidence goes in too: the count from each pass, and every note whose `updated_at` is more than 13 hours after its `created_at`, a call published late that a single-pass sweep would have lost. When a field looks wrong, check whether a person edited it in the CRM before blaming the previous run.

## Rules
- Oldest first, so a newer call's reading of the pipeline wins over an older one.
- Never route a talent, partner or investor call into the sales pipeline as a deal.
- Never set a closed status, never move one backwards, never overwrite what a person set from the same call.
- Never create a company from a personal-mail domain, and never create a record for someone who was only prepared for or named.

## Output
The run report: the window, the count from each pass and any late-published note; then every call with its class and outcome; for each routed call, the records created or updated, the status transition or the one declined with the reason, the deal value written or deliberately left empty, any duplicate the read-back caught and repaired, and each judgment call made. A window with nothing to do reports "nothing new".