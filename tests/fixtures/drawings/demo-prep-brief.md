# Prepare a demo brief from past calls and the CRM

**When to use it**: a demo is on the calendar with a company you have already spoken to, and preparing properly means re-listening to calls, rereading the deal and checking what changed, which is the work that gets skipped when the week is full. The run joins those sources into one brief and a slide plan built from what this buyer actually said, and posts both before the meeting.

```
              Natural language input in Claude
              "Prepare tomorrow's demo from everything they told us on the last calls."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Find the demo and who is coming            │   calendar_event
│  The event, its guests' emails, and the company │
│  domain those emails share.                     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Resolve the company, deal and contacts     ║   hubspot_object, hubspot_property
║  One company by domain, its most recent open    ║   an ambiguous match is asked, never guessed
║  deal, every contact and free-text field.       ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no company        another spelling or the domain
                         ├───────────────▶  ▪ no open deal      attach a deal to the company first
                         ▼  one company, one deal
┌─────────────────────────────────────────────────┐
│  3 · Pull every call and note with them         │   granola_content
│  Recorded calls matched on guest email and on   │   hubspot_object engagements
│  domain, plus the CRM's notes and meetings.     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Lift quotes, objections and commitments    ║   a quote is verbatim or it is dropped
║  Their problems in their own words, what was    ║
║  left unresolved, and who owes what.            ║
╚════════════════════════╤════════════════════════╝
                         ▼
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  5 · Flag who never spoke        │  │  6 · Check what changed since    │   serper_search, linkedin_unipile_profile
│  Contacts on the HubSpot record  │  │  News since the last call, and   │
│  who never appear on a call.     │  │  role changes on their profiles. │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼  the whole picture since the last call
┌─────────────────────────────────────────────────┐
│  7 · Plan the deck from their problems          │   drive_file op=export
│  One slide per problem they named, one per open │   reads the template's slides, never edits it
│  objection, brackets where a number goes.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Post the brief before the meeting          │──▶  Slack  the brief first, the slide plan in its thread
│  Lead with what matters most: an unresolved     │   slack_post_message
│  objection or a stakeholder who never spoke.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  9 · A human builds the deck                    │   the one human step
│  Copy the template in Google Slides and apply   │
│  the plan by hand; no connector writes slides.  │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there, writes nothing and says why
```

**Nothing is sent outward and nothing is written to the CRM.** The run reads the calendar, the CRM, the call recorder and public sources, posts one internal message, and reads the deck template without touching it. Step 2 stops with a one-line answer instead of guessing, and every later step tolerates a missing source by saying so in the brief. It runs just as well on demand for a named company as each morning over the next day's demos.

## 1. Find the demo and who is coming
- <tool:calendar_event> — op=list with `time_min` now, `time_max` at **[the end of tomorrow]** and `query` set to **[the word your demo invites carry]**, then op=get on the event for its description and attendee list.
- The external guests are the attendee emails minus your own domain, meeting-room resources and notetaker bots. The domain they share is the company key for everything after this step. A guest on a personal webmail address carries no domain: look that one person up by email in step 2 instead of guessing a company from the event title.
- Keep the guest list itself. A person invited to this demo who has never been on a call is one of the things step 5 flags.

## 2. Resolve the company, deal and contacts
- <tool:hubspot_object> — op=search on contacts with an `IN` filter on the guests' emails, returning the associated company id; failing that, op=search on companies with `domain` `EQ` the domain. **A domain is exact, a company name is not**: a name search is a token match and two companies can share one. Several matches are listed with domain and country and the person running it chooses; nothing is picked silently. No match stops the run with "No company matched", and a suggestion to try the domain or another spelling.
- The deal is the company's most recent open one (`hs_is_closed` false, latest `createdate`) unless the request names another. A company with no deal stops the run: the brief needs one deal to read the pain and the use case from.
- op=get on the deal for stage, amount, close date, owner and create date, plus the date-entered property for its current stage (<tool:hubspot_property> lists its internal name), which tells a fresh deal from one that has sat still. An open deal whose close date is already in the past has slipped, and the brief says so. Property history is not exposed through this call, so the brief never claims how many times it slipped.
- <tool:hubspot_property> — op=list on deals and on companies, keeping the custom free-text fields: `fieldType` text or textarea, `hubspotDefined` false, minus **[a short denylist of noisy fields]**. Then read those on the deal and the company and keep every non-empty one, labelled by its label. Discovering the fields instead of hard-coding them means a qualification field sales added last month is read without editing the process.
- The company's contacts with name, email, job title and `hs_language`. The deck language is the one the request names, else the main contact's `hs_language`, else **[your default]**; when the request and the record disagree, the brief says so in one line.

## 3. Pull every call and note with them
- <tool:granola_content> — op=list_notes with `created_after` at the company record's create date (a first call often predates the deal), paged by `cursor` at a `page_size` of 30. Read each note's `attendees`, with op=get_note where the listing does not carry them, and match them **twice**: once on the guests' emails and once on the company domain. A call is recorded by whoever hosted it, so the person you are meeting may sit on a colleague's call rather than their own, and the domain pass is what finds the first call a different person at the company took.
- Two notes can be one call: when two of your colleagues attended, each owner gets a note. The same calendar event id, the same attendees and the same slot mean the same call, read once, keeping both links.
- A note whose only attendees are your own team is preparation, not a call with them, and nothing in it is quoted as theirs.
- Read the summary first. Take the transcript only for the passages you will quote: op=get_note with `include` transcript, or op=get_transcript page by page when that answers `TRANSCRIPT_TOO_LARGE`. Transcripts mangle company and product names, so take spellings from the email domain and the CRM, never from the transcript.
- <tool:hubspot_object> — op=get on the deal and on the company with `associations` set to notes, meetings and calls, then the bodies (note body, meeting body and internal notes, call body, timestamp): **[the ten most recent]** per type per object.
- Every source gets an id (the note id, the engagement id, the property name) so a quote can name where it came from. Another call recorder can stand in for this one; the matching rules stay the same.
- **No recorded call at all**: the brief says so on its first line and is built from the CRM history and public research alone. That is a weaker brief, and it is labelled as one.

## 4. Lift quotes, objections and commitments
Four things from each call and nothing else: the problems they described, in their words; every objection, and whether it was resolved on the call; commitments made by either side, with who owes what; the names and roles of everyone who spoke.
- **A quote is a verbatim substring of one raw source, or it is dropped.** Compare against the raw text, character for character: same accents, same spacing, same apostrophes. A quote the model tidied (a curly apostrophe for a straight one, a dropped accent) fails the check, is dropped, and is listed under warnings in the brief. A missing quote is better than a fabricated one.
- **Keep the prospect's words apart from your rep's.** A pain field filled in on the CRM is your salesperson's summary; a transcript line is the buyer. The brief keeps three blocks, in this order: CRM fields, CRM engagements, call transcripts, each quote tagged with its source.
- Do not translate their problem into your category names. "We lose two days a month reconciling the two systems by hand" is the sentence to repeat back in the room; compressed into "integration challenges" it describes every account you have.

## 5. Flag who never spoke
- Compare the contacts on the company and the deal with the attendees and speakers across every call from step 3. **A stakeholder with a role on the record who has never appeared on a call is the most useful line in the brief**, and the easiest to miss by hand, because it only shows up when two systems nobody compares are compared.
- Flag every guest on this demo's invite who has never been on a call as well: a new face in the room usually means a new decision-maker or a new objection.
- Say which kind of meeting this is: a deal that just opened, a deal that has sat in its stage for a long time, or one that has already slipped its close date.

## 6. Check what changed since
- <tool:serper_search> — kind=news on the company name in quotes plus its domain or one distinguishing word, with `tbs` covering the time since the last call. Set `country` and `language` explicitly, since both default to French. Keep only results tied to this company, each with its URL: a funding round, a leadership change, a launch, a reorganization.
- <tool:linkedin_unipile_profile> — op=person for each attendee, by public slug (the numeric member id from a search is rejected). The slug comes from the contact's LinkedIn URL on the record, or from <tool:serper_search> with `site_filter` set to linkedin.com/in on the name and company, kept only when the result names the same company. Compare the current position against the job title on the CRM record and the one they gave on the last call. Check that the returned public identifier is the one you asked for before using it. A `throttled_sections` value means LinkedIn rate-limited that section, not that it is empty: retry it later in a catch-up pass and keep concurrency at 8 or fewer.
- Personal details that surface anywhere (health, family) stay out of the brief.

## 7. Plan the deck from their problems
- <tool:drive_file> — op=export on **[your master demo deck]**, which returns a Slides file as text, so the plan names slides that actually exist. The template is read, never edited or copied from here.
- One slide per problem they actually named, in the order they raised them, each carrying its verbatim quote. Three named problems means three problem slides, not every problem slide the template holds.
- Every objection left unresolved in step 4 gets its own slide. Skipping it only means it gets raised in the room with less preparation.
- Feature slides only for what they asked about, each marked shipped or beta as it stands in **[your product status source]**. A case study and a logo slide only for customers who have agreed to be named, as recorded in **[your reference list]**, matched on the prospect's industry and size.
- Numbers only as they stand in **[your approved figures]**; everywhere else a bracketed placeholder. A confident wrong figure said out loud in a demo costs more than a visible gap the presenter fills.
- The plan is a keep or delete call per template slide, plus the placeholder values (company name, first name, one quote per problem slide).

## 8. Post the brief before the meeting
- <tool:slack_post_message> — to **[your deal-desk channel]**, by channel id pinned in the process: listing every channel to find it on each run carries a large payload for a lookup that never changes. The bot has to be a member of that channel, or the post fails at the very end of the run.
- The brief goes first and the slide plan second, as a reply in the brief's thread using the `ts` it returns. The brief is what someone reads on the way into the room; the plan is what they work from at their desk. Lead with the single most important thing, usually an unresolved objection or a stakeholder who has never spoken.
- A message above about 4,000 characters is split into threaded parts, not truncated, and `split_into` in the response says so. A Slack message can be deleted but not edited, so never re-post on the assumption that it was cut off.

## 9. A human builds the deck
The presenter copies the template in Google Slides and applies the plan by hand: delete the slides marked delete, paste the quotes exactly as written, fill the placeholders, and leave every bracket visible until they have a real number. No connector writes to Slides, so this stays a human step, and the template itself is never overwritten.

## Output
Report: the calls found and their dates, the problems quoted and the quotes dropped at the verbatim check, unresolved objections, stakeholders and new guests who have never spoken, what changed since the last call with its sources, the slides kept and deleted, and any source that was unavailable for this run.