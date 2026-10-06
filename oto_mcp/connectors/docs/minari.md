## prerequisite — minari api key

create an API key in Minari (**Settings → API & webhook**), then paste it into oto.
- the key carries the rights of the **whole company**, not of a single user: it sees the calls of all the reps in the workspace, and everything it writes is written on behalf of the company
- byo-only: no shared oto key. it is your call log
- **60 requests per minute, per company** — not per key or per person: two automations running under the same key share this budget. a `429` comes back with the number of seconds until reset

## usage — transcribed calls, call lists, team analytics

minari is an outbound call dialer: the team dials, minari records, transcribes, summarizes and detects objections. six tools:

- "what was said about pricing this week?" → `minari_call(op="list", transcript_search="prix", start_date="…", end_date="…")`
- "which calls led to a meeting?" → `minari_call(op="list", status=["meeting-booked"])` — ⚠️ `meeting-booked` is a **filter-only** value: a call row never carries it as its `status`, it carries the boolean `meeting_booked`
- "the summary and objections of this call" → `minari_call(op="get", call_id="…")` — returns the record **without** the transcript (see the limits below)
- "the full verbatim" → `minari_call(op="transcript", call_id="…")`
- "is there a recording?" → `minari_call(op="recording", call_id="…")` — says whether it exists and its size, **without** pulling the audio; to let someone listen, use the call record's `public_call_link`, a page shareable without a key — **null** if external sharing is disabled for the company, so check before promising a link
- "what is our pickup rate this month?" → `minari_analytics(op="overview", start_date="…", end_date="…")`
- "who picks up the most on the team?" → `minari_analytics(op="users", start_date="…", end_date="…")`
- "what do we get stuck on most, and do we handle it?" → `minari_analytics(op="objections")`
- "which lists are stalled or exhausted?" → `minari_analytics(op="lists", period="week", call_limit=3)`
- "push this prospect list into the dialer" → `minari_user()` for the rep's id, then `minari_list(op="create", name="…", assigned_to=…, contacts=[…])`
- "add these 50 contacts to the list in progress" → `minari_contact(op="add", list_id="…", contacts=[…])`
- "declare an `industry` field before the import" → `minari_custom_field(op="create", field_id="industry", label="Industry")`

an imported contact requires at least one of `firstName`, `lastName`, `email`; it also accepts `company`, `title`, `companyDomain`, `linkedinUrl`, `description`, `phoneNumber1`…`phoneNumber5`, a `note` (5000 characters) and `customFields` declared beforehand.

## note — ⚠️ lists only show CSV imports

this is the API's trap no. 1, and it is silent: `minari_list` and `minari_contact` see **only the CSV import source**. if your contacts come from HubSpot, Salesforce or another CRM sync, your lists do exist in Minari but will **never appear** through these tools — an empty result means "no CSV list", not "no list".

the **all-sources** view is `minari_analytics(op="lists")`: it is the one that answers "where are my lists at". calls and analytics, for their part, cover all sources.

## note — read limits

three responses can blow up, and the connector bounds them **while saying so** rather than truncating silently:

- **the call record embeds the whole transcript.** `minari_call(op="get")` therefore removes it and returns `transcript_utterances` (the number of utterances); the text is obtained via `op="transcript"`, the endpoint Minari split off precisely for this
- `op="transcript"` stops at **200 utterances** by default (`max_utterances`) and returns the real total plus a `truncated` flag
- **a list returns its 1500 contacts in one block**, with no pagination: `minari_list(op="get")` stops at **100** contacts by default (`max_contacts`) and returns `total_contacts` plus `truncated`

pagination: `minari_call(op="list")` and `minari_list(op="list")` return 50 rows per page, `minari_analytics(op="lists")` returns 10 — sizes fixed by Minari, not negotiable. every response carries a `next_cursor`.

⚠️ **the cursor is a POSITION, not a query** — it decodes to `{"s": <start date>, "c": <call id>}` and carries no filter. turning the page while passing ONLY the cursor therefore returns the rest of the **entire** log, unfiltered, without any error: you must pass the same filters alongside the cursor. the response reminds you in `note` every time a next page exists.

## note — analytics default windows are not the same

`op="overview"` and `op="users"` cover **today** when no dates are given; `op="objections"` covers the **last 7 days**. asking for "our numbers" without specifying a period therefore does not return "everything", but the current day — and the response always states in `period` the window actually used, to be read before concluding.

rates are percentages (0–100) and are `null` when their denominator is zero. analytics are day-granular.

two parameters of `op="lists"` are **required** because they define the vocabulary: `period` (the call counting window) and `call_limit` (the number of attempts after which a never-reached contact is considered exhausted). two different values give two different answers — they are two questions, not an inconsistency.

## note — written from the contract, not yet verified live

this connector was written from the OpenAPI 3.1 published by Minari (`api.minari.ai/docs/openapi.json`) and its usage guide for agents, **without a probe against a real account** (2026-08-31). everything above is therefore a reading of the contract, not a measurement.

the first real test is the **"test the connection"** button on this card: it reads the company's members, which proves the key authenticates. it does not judge what it returns — an empty directory is not grounds for refusal, and the probe runs before saving: what it refuses would never be saved.

## note — what minari does not expose

the public API allows **neither triggering a call**, nor editing an existing contact, nor managing users. these actions are done in Minari. webhooks (`call.completed`, `call.meeting_booked`, `contact.updated`) are also configured from **Settings → API & webhook**: there is no endpoint to create or list them, hence no tool here.

deleting a list (`minari_list(op="delete")`) or a custom field (`minari_custom_field(op="delete")`) is **permanent** and removes the list from the rep's call queue. removing contacts from a list without destroying it is `minari_contact(op="remove")`.
