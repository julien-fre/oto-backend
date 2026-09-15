# Prepare a brief before every customer call

**When to use it**: before customer meetings, when whoever takes the call would otherwise skim the CRM and go in without knowing whether the account has used the product since the last conversation, or where the things it asked for now stand. The calls, the account record, product usage and the tracker each answer a question the others cannot; this joins them into one brief, posted before the call and led by the most serious thing that is true.

```
              Scheduled routine, every weekday morning
              "Brief me on today's customer calls."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Find today's customer meetings             │   calendar_event
│  Each invite with an attendee whose domain      │   attio_record  one search per domain
│  belongs to a paying account.                   │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not a customer    internal or prospect meeting
                         ▼  one brief per customer meeting
┌─────────────────────────────────────────────────┐
│  2 · Quote requests from past calls             │   granola_content
│  Recorded calls with the account, each ask      │   data_rows  a note cache, refreshed once a run
│  kept in the customer's own words.              │   matched on attendee email, then domain
└────────────────────────┬────────────────────────┘
                         ▼
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  3 · Recap plan, renewal, people │  │  4 · Compare usage to the calls  │   posthog_query
│  Plan, renewal date and people   │  │  Weekly active users over the    │   posthog_group
│  on the account record in Attio. │  │  quarter, and features dropped.  │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Give each request its status               │   linear_issue  search, read only
│  Each ask matched to its issue by substance,    │
│  and every ask with no ticket named.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Post the brief, biggest risk first         │   slack_post_message
│  Falling usage, a departed champion, a missed   │   the detail in the thread
│  commitment or a near renewal leads.            │
└─────────────────────────────────────────────────┘

▪ terminal — no brief is prepared for that meeting
```

The note cache in step 2 is refreshed once per run, before the first meeting; steps 2 to 6 then run once per customer meeting. Steps 3 and 4 both read from step 2 (the attendees and what was said) and are independent of each other. Every call in this process is a read except the brief itself, one row on the briefs log and the rows of the note cache.

## 1. Find today's customer meetings
- <tool:calendar_event> `op="list"` on each calendar you brief for, `time_min` = now, `time_max` = the end of the working day, `max_results` set above your busiest day's meeting count. The default is 20 with no cursor, so a busy calendar drops meetings without a warning. The list does not carry guests, so `op="get"` per event for its `attendees`. Run early enough that the first brief lands **[an hour]** before the first meeting. A meeting booked after the run gets no brief unless you add a later run; the log below keeps a second run from briefing anything twice.
- Drop attendees on your own domain, room resources and note-taker bots. What remains are the external domains.
- <tool:attio_record> `op="search"`, `object="companies"`, `filter={"domains": "<domain>"}`, **once per distinct domain across the day**, not once per attendee. `query` matches the record name only: a domain passed as `query` finds nothing and looks exactly like "not a customer". An attendee on a free-mail address resolves by person instead (`object="people"`, `filter={"email_addresses": "<email>"}`), then through that person's company.
- Keep the meeting only when the company is a paying customer by **[your customer-status attribute]**. A prospect call belongs to sales prep; an invite with no external attendee belongs to nobody.
- <tool:data_rows> on your briefs log, keyed `<event id>:<start>`. A meeting already briefed is skipped; a meeting moved to another time has a new key and is briefed again.

## 2. Quote requests from past calls
- <tool:granola_content> `op="list_notes"` **once per run, not once per meeting**, `page_size=30`, walking `cursor` to the end. The first run lists `created_after` = **[your lookback, e.g. twelve months]** ago; every later run lists only `updated_after` = the previous refresh. Take every note whoever owns it: a call is recorded by whoever hosted it, and a read limited to your own notes misses the calls a colleague ran.
- The listing carries neither attendees nor the calendar event, and there is no search on notes. So `op="get_note"` on each note the listing returned, for its `attendees` and its calendar event id, and <tool:data_write> one row per note id on a note cache, upserted on that id: attendee emails, their domains, calendar event id, date. Only the first fill reads the whole lookback; pace it under the connector's rate limit. Without the cache, every meeting would re-read every note in the workspace.
- <tool:data_rows> on the cache for each meeting. Match twice: first `filter={"attendee_emails": {"contains": "<email>"}}` for each of this meeting's attendees, then separately `filter={"domains": {"contains": "<domain>"}}` for the company's domain. The colleague who joined one call from the same company is only on the second list.
- **Two notes can be one call.** When two of your people attend, each gets a note with its own id. Collapse cached notes that share a calendar event id before extracting, or every request is counted twice.
- **An attendee list is not proof of a conversation with them.** A prep note, or a sync where two colleagues discuss the account, can carry the customer's address on the invite. The tell is grammatical: nobody talks about a customer in the third person with them in the room. Skip those.
- `op="get_note"` again on each call kept for this meeting: the cache holds who and when, not what was said, and the summary is the source. Open the transcript only when a request needs its exact words and the summary paraphrased them: `include="transcript"` can come back `TRANSCRIPT_TOO_LARGE` on a long meeting, then `op="get_transcript"`, paginated from the start.
- Extract only what carries forward, each with its call date: anything they asked for and whether it was promised; anything either side committed to, with a date; any complaint and whether it was resolved.
- **Quote each request in their words.** Step 5 matches it against the tracker, and a paraphrase into internal vocabulary is exactly what makes that match fail.
- No recorded call is an answer, not an error. The brief says so and leads on the account and on usage.

## 3. Recap plan, renewal, people
- <tool:attio_attribute> `op="list"`, `target="objects"`, `identifier="companies"`, once per run. Plan, renewal date and owner live under **[your workspace's attribute slugs]**; read them, never assume them. For deals, `op="statuses"` with `identifier="deals"`, `attribute="stage"` gives the stage titles that count as open.
- <tool:attio_record> `op="get"` on the company: plan, renewal date, owner and the people linked to it. Note whether the renewal falls inside **[ninety days]**, because that changes what the brief leads with. Then the open deals on that company, from `object="deals"` filtered on the associated company and an open stage.
- <tool:attio_note> `op="list"` with `parent_object="companies"` and the company's `parent_record_id`. Notes come back oldest first, ten a page by default, with no sort and no date filter, so page `offset` until a page comes back short: the recent support history and hand-written promises are at the end.
- **The departed champion check.** For every external attendee from step 2's calls, <tool:attio_record> `op="search"`, `object="people"`, `filter={"email_addresses": "<email>"}`. One who is no longer among the company's people, or whose person record now points at another company, is flagged "may have left, confirm". Never state it as fact: someone who was simply never added to the CRM looks identical. When it is true, it is the most important line the brief can carry.

## 4. Compare usage to the calls
This is the half a CRM cannot answer.
- <tool:posthog_group> `op="types"` first, for the index of your account-level group. An empty answer means the project has no group analytics: fall back to the people on the company's email domain, and say in the brief which of the two was used.
- <tool:posthog_group> `op="find"` with `group_key` = the company's domain when your group key is the domain, otherwise `op="list"` with `search` = the company name from the account record. Record in the brief which key matched: a name search can land on a similarly named account.
- <tool:posthog_schema> before writing any query, for the real property and event names.
- <tool:posthog_query> with `hogql`: weekly active users for the account over the last **[13 weeks]**, `uniq(person_id)` grouped by `toStartOfWeek(timestamp)`. Never `count(distinct distinct_id)`, which counts devices, not people. Read the series week by week: a flat quarter can hide a halving followed by a recovery.
- A second query over **[your core feature events]**: the count in the last **[30 days]** against the **[60 days]** before. A feature used every week until two months ago and untouched since is a specific, answerable question for the call. Read depth as well as activity: whether they use **[your core feature]** for what they bought it for, or only the edges around it. Leave page views and autocapture out, and exclude your own team's addresses: a support session inside the customer's workspace reads as usage.
- Aggregate in the query and add your own `LIMIT`. An unbounded query is capped at 101 rows, and a truncated week list looks like a drop.
- **Zero events and no match are different answers.** An account not found in analytics prints "no usage data", never "no usage".
- Then set usage against step 2. "We're rolling it out to the wider team next month", said on a recorded call, next to a user count that has not moved since, is the most useful contradiction a brief can carry. Neither system shows it alone.

## 5. Give each request its status
- <tool:linear_issue> `op="search"` per request, in two or three phrasings: the customer's words, the symptom, and the name your team would give the feature. A customer asking for "a way to see who changed what" and an issue called "audit log" are the same request.
- `op="get"` on the best candidate and compare its description with the quote before calling it a match. Shared words in a title are not a match.
- Report the real status: shipped (a completed state, dated after the request), in progress with its target, backlogged, or declined (a canceled state). A request promised for a date whose issue is still open past that date is a **missed commitment**.
- **No matching issue: write "no ticket found".** It is an item for the call, not an omission. Reporting "no ticket" for something that shipped weeks ago is worse than no brief, because the call then apologizes for it. This process never creates or edits an issue; filing what customers say is a separate process.

## 6. Post the brief, biggest risk first
- <tool:slack_post_message> to **[your customer team's channel]**, one message per meeting. The first line names the account, the meeting time, and whichever of these is true and most serious: usage has fallen materially, a champion may have left, a commitment was missed, the renewal is inside [ninety days]. The brief is opened ninety seconds before the call; a departed champion in the fourth paragraph is not in the brief.
- Everything else goes in one thread reply on the message's `ts`: what they asked for and where each request stands, what they use and what they stopped using, open commitments on both sides, the people to confirm.
- <tool:data_write> one row on the briefs log, keyed `<event id>:<start>`, with the account, the lead line and the message `ts`, before moving to the next meeting.

## Output
Per meeting: the calls found once duplicates are collapsed, requests extracted and how many matched an issue, requests with no ticket, the usage direction over the quarter, features dropped, people flagged as possibly gone, open commitments, and the lead line chosen. For the run: meetings skipped as internal, prospect or already briefed.