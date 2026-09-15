# Draft first messages to new LinkedIn connections

**When to use it**: your outbound sends LinkedIn invitations, people accept, and then nothing happens, or a generic pitch lands in a thread where someone on your side already wrote months ago. This reads every newly accepted connection, works out where it came from, closes anyone you have already talked to, and writes a two-message sequence built on one verified public fact. Nothing is sent until a person has decided to send that exact text.

```
              Scheduled routine, weekday mornings
              "Go through my new LinkedIn connections and queue the first messages for my review."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the seat before spending             ║   linkedin_unipile_account
║  The LinkedIn seat answers alive, and the pool  ║   data_rows
║  table can be read and written.                 ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read the accepted connections              │   linkedin_unipile_network
│  Each connection not yet in the pool becomes    │   newest first
│  one row, keyed on its LinkedIn URL.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Assign where each one came from            │   data_rows on the source lists
│  Match your outbound source lists; anyone on    │
│  none of them is organic and left alone.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ organic            personal network, out of scope
                         ▼  from one of your outbound sources
╔═════════════════════════════════════════════════╗
║  3 · Close anyone already talked to             ║   linkedin_unipile_chat
║  Read the whole thread, then the exclusions,    ║   data_rows on the exclusions
║  one person per company and the email window.   ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ already contacted  a message from you exists
                         ├───────────────▶  ▪ excluded           listed, a colleague, or emailed
                         ▼  a blank thread and nothing excludes them
┌─────────────────────────────────────────────────┐
│  4 · Research, then judge the fit               │   linkedin_unipile_profile
│  Confirm the role, read the site and the open   │   firecrawl_scrape
│  jobs, then a verdict: go, weak or no go.       │   theirstack_jobs_search
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no go              outside your fit guide
                         ▼  go or weak
┌─────────────────────────────────────────────────┐
│  5 · Write the two messages                     │   data_write to the approval queue
│  An observation and its question, then a new    │
│  angle; ten proposals per run at most.          │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · A human decides each pair                  ║──▶  Approval queue  one decision per person
║  Send, rewrite or drop, with the reasons        ║
║  copied to the log the judge reads next time.   ║
╚════════════════════════╤════════════════════════╝
                         ▼  decided send
╔═════════════════════════════════════════════════╗
║  7 · Re-check right before sending              ║   linkedin_unipile_chat
║  Accepted, copy present, thread re-read now,    ║   linkedin_unipile_profile
║  still in the role, no recent email.            ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ to fix             a check failed, nothing sends
                         ├───────────────▶  ▪ not accepted yet   the invite is still pending
                         ▼  every check passes
┌─────────────────────────────────────────────────┐
│  8 · Send, then follow up once                  │──▶  LinkedIn message  exactly as approved, capped per run
│  Message one as approved, message two after     │──▶  Tracking table  step, dates and any reply
│  five business days only if nobody replied.     │   linkedin_unipile_chat
└─────────────────────────────────────────────────┘

▪ the row stops there on this run, and nothing is sent to that person
```

Steps 3 to 5 loop over every row still open in the pool. Step 6 happens between runs: a person decides in the approval queue, and the next run collects those decisions before it re-checks and sends. A row sent back for a rewrite goes through step 5 again with its comment, then returns to the queue.

**The tables, and the one key they share.** Every match in every table runs on the normalized LinkedIn URL: `https://www.linkedin.com/in/<public identifier>`, no trailing slash, no query parameters.

| Table | What it holds |
|---|---|
| The pool | The process's memory: one row per person, and its status is the truth. A person enters once, is judged once, and is never messaged twice. |
| The approval queue | The only table the reviewer works in: undecided pairs only, never more than ten. |
| The tracking table | One row per person messaged: step, dates of both messages, reply. Written by the run, never edited by hand. |
| The source lists | The LinkedIn URLs of people in your email campaigns, with their tier, and of people your sourcing tool found. |
| The exclusions | People, domains and companies you never contact: opt-outs, customers, friends, people who replied to a campaign and their colleagues. |
| The decisions log | One dated line per reviewer decision, with its reasons and evidence. The judge and the writer read it before every run. |

## 0. Check the seat before spending
- <tool:linkedin_unipile_account> `op="status"` must return `connected` and `alive`. A seat can stay linked while its session is dead, and this call is free.
- <tool:data_rows> on the pool must answer. If either check fails, stop and say which: a run that cannot read the inbox or write the pool spends nothing.
- If the LinkedIn tools are missing from the toolbox altogether, the connector is most likely paused rather than missing a key. Report it that way, not as a credentials problem.

## 1. Read the accepted connections
- <tool:linkedin_unipile_network> `op="relations"`, newest first, with `fields=["name","headline","public_identifier","member_id","created_at"]` so each page stays small. Keep paging until a whole page is already known to the pool. Dedupe on `member_id`, never on the page offset: the cursor encodes a volatile offset that can return the same person twice.
- Look each normalized URL up in the pool and in the exclusions. A known person stops there, with one exception: a known row with no `connected_on` is an invite you queued earlier (for example, a campaign lead with no valid email address, invited by hand). When it appears among the accepted connections, stamp `connected_on` and the row resumes where it was.
- A new person becomes one row through <tool:data_write>: name, headline, company, URL, `connected_on`, status `to_draft`, fit `to_judge`. Nothing else is written at this stage.

## 2. Assign where each one came from
Compare the normalized URL, in this order:
1. **Your email campaigns' list.** The provenance is the tier carried on that row. Reload the list whenever a campaign starts: a list that lags turns a sourced lead into an organic one.
2. **Your sourcing tool's list**, matched on the public identifier.
3. **Neither: organic.** Add the person to the exclusions with the reason `organic` and set the pool row to `rejected`. They are not judged again unless a person takes them off the exclusions.

**Never guess a provenance.** A connection that looks like a sourced lead (someone in the segment you are currently working) but is missing from both lists waits in `to_draft` with the note "source to confirm, reload the list". It neither passes for organic nor gets messaged as sourced.

## 3. Close anyone already talked to
This is the step that protects the relationship. For each open row:
- **Find the thread.** <tool:linkedin_unipile_chat> `op="list"` returns at most 25 threads per page: page by `cursor` and index the one-to-one threads on `attendee_profile_url`, normalized like every other URL, once per run rather than once per person. When the name enrichment did not run, the response says so in `attendee_names`: fall back to the attendee's provider id instead of concluding there is no thread. **Only a listing paged to the end proves there is no thread.** A partial listing raises no error, it just shows fewer people.
- **Read it in full.** `op="read"` with the thread id. Any message written from your seat, however old, closes the row as `already_contacted`: tick `thread_verified` and note the date of your last message. Campaign files are not evidence. The inbox is.
- **Decide who wrote each message** by comparing its `sender_id` with your own seat's identity from <tool:linkedin_unipile_profile> `op="me"`. Do not rely on `last_message.is_sender`: it has been seen false on every thread of an account, including threads the account wrote last.
- **An unreadable thread is not a blank thread.** An error or a timeout leaves the row in `to_draft`, to be read again next run.
- **Exclusions.** The person, their company's domain or their URL on the list sets the row to `rejected`, with the list's reason.
- **One person per company.** When two open rows share a company, keep the one closest to the buying decision and reject the other as "colleague, one person per company".
- **One channel at a time.** If anyone at that company received an email from your campaigns in the last **[7 days]**, or is still in a live email sequence, the row waits in `to_draft` with the note "recent email". Read this from **[your email send log]**. If the log cannot be read, the row waits and the report says why.

A row moves to step 4 only with `thread_verified` ticked and no exclusion touching it.

## 4. Research, then judge the fit
Load **[your fit guide]** and the decisions log before judging. A reason or a strength that recurs in the log counts as a rule until the reviewer contradicts it, and the evidence pasted there tells the research where to look.
1. **Confirm the person is still there.** <tool:linkedin_unipile_profile> `op="person"`, `identifier=<public identifier>`. Check that the returned `public_identifier` is the one you asked for, because a mismatch is a redirect onto someone else. Judge the *current* company, and correct `company` and `job_title` on the row when the profile disagrees. Keep the returned `provider_id`, which sending needs. A response carrying `throttled_sections` is an upstream rate limit, not missing data: retry that person later.
2. **Read the profile's description before anything else.** A description that matches **[your standing no-go profiles]** is an immediate `no_go`, however strong the lead looks. So is a contact address on a different domain than the stated company.
3. **Gather public sources.** <tool:firecrawl_scrape> on the company's home, pricing, customers, team and careers pages, with `max_age` so a stable page comes from cache. <tool:theirstack_jobs_search> with `extra={"company_domain_or": [<domain>]}` and a small `limit`: it bills per company record returned, and an empty result is normal on small companies. Never read an empty result as "not hiring". The person's LinkedIn activity may steer the angle, but it is never quoted.
4. **Write the verdict**: `go`, `weak` or `no_go`, with two or three signals written one per line as "fact (source: URL)", a reason, a trigger and an angle, all on the pool row. **A signal without a URL actually opened does not exist.** A `no_go` ends the row as `rejected`, and nothing more is spent on it.

## 5. Write the two messages
Load **[your copy guide]** and re-read the rewrite comments in the decisions log first: they are copy defects already flagged once. For each `go` or `weak` row, most recent connection first:
1. **Message one: an observation and its question.** One verified public observation about how this company handles the problem you solve (from a URL you opened), then one question about their workflow that can be answered in a line. No pitch. **The twenty test:** if the message could go unchanged to twenty people with the same title, it is research, not relevance. Rewrite it.
2. **Message two: a different angle.** One question. Never a reminder of message one and never an exit line ("should I close this?"). For a row returned with a rewrite comment, the comment outranks everything else: rewrite to it rather than adjusting at the margin.
3. **Run the copy checklist on both messages**, item by item. A message that fails is rewritten, never proposed.
4. **Queue the pair.** <tool:data_write> one row per person in the approval queue: name, company, title, URL, message one, message two, and a one-line "why" (source, verdict, the observation used), with the decision empty. On the pool row, store both messages and set the status to `awaiting_decision`.

**Cap: ten proposals per run, and never more than ten undecided rows in the queue.** The rest stay `to_draft` for the next run. A `weak` row gets a shorter, more careful message, never a generic one. When no observation holds up, the row stays `to_draft` with "no solid observation", and the report says so.

## 6. A human decides each pair
The reviewer opens the approval queue and sets one decision per row, covering both messages:
- `send`: message one goes out exactly as written, character for character, and message two follows under the same rule. The reviewer can tick what made it a good lead.
- `rewrite`, with a comment: step 5 rewrites it on the next run and queues it again.
- `no`: the person is never messaged. Writing "exclude" in the comment adds them to the exclusions.

For `no` and `rewrite`, the reviewer ticks the reasons and pastes the evidence: a URL, a sentence from the site.

The next run **collects the decisions first**. `send` sets the pool row to `approved`, `rewrite` sets it to `to_fix` with the comment, `no` sets it to `rejected`. Reasons, strengths, evidence and comment are copied onto the pool row and appended as one dated line to the decisions log **before** the row leaves the queue, so no feedback is ever lost. When the same reason shows up three times in the log, the report proposes it as a rule for the fit guide or the copy guide, and the reviewer confirms it.

**Make the pool table enforce this.** With <tool:data_patch_schema>, give message one and `thread_verified` a `required_when` on the status, for `approved` and for `sent`, and declare the status `lifecycle` so a row cannot jump from `to_draft` straight to `sent`. Rows approved without copy would otherwise go out empty, and a refusal at write time catches what a check at send time can miss.

## 7. Re-check right before sending
For each `approved` row, run the checks in this order. One failure and nothing sends: the row goes to `to_fix` with the reason in plain words, and the report names the check.
1. **The connection is accepted.** `connected_on` is set. If it is not, the row is a pending invite: it stays `approved`, nothing sends, and the report lists it under "awaiting acceptance". This is the only check that does not count as a failure.
2. **The copy exists.** Message one is not empty, and neither is message two when the row is headed for the full sequence.
3. **The thread is blank, read just now.** <tool:linkedin_unipile_chat> `op="read"` again. Never trust step 3, which may be days old. A message from your side that appeared since then closes the row as `already_contacted`. An unreadable thread postpones the send, it never allows it.
4. **The person is still in the role.** <tool:linkedin_unipile_profile> `op="person"`. If the company changed, the row goes to `to_fix` with "no longer there, re-judge on the new company", because an observation written for the old employer means nothing now. If it matches, stamp `role_checked_on`. A check older than **[7 days]** no longer counts.
5. **The email window is clear.** The step 3 rule, read again now.

**A check that cannot be run counts as a failed check.**

## 8. Send, then follow up once
**Message one**, for each row that passed, in the order the decisions were made:
- <tool:linkedin_unipile_chat> `op="send"` with the approved text, unchanged. Pass the thread id when a thread already exists, otherwise `recipient_id` set to the `provider_id` from step 4.
- <tool:data_write> sets the pool row to `sent` with `sent_on`, and creates the tracking row: name, company, source, URL, the message sent, step `message_1_sent`, date.

**Message two**, for each `sent` row at least **[5 business days]** old with no `m2_sent_on`:
- Read the thread first. If the person replied, message two never goes: note it, flag it in the report, and the conversation belongs to a person from here on.
- Otherwise send message two exactly as approved, stamp `m2_sent_on`, and move the tracking row to `message_2_sent`.

**Pacing:** at most **[15 messages per run]**, messages one and two combined, on business days and within **[your business hours]**. Rows past the cap wait for the next run, and the report counts them.

**Tracking**, on every `sent` row at each run:
- **A reply:** the tracking row moves to `replied` with the date and a one-line summary. Colleagues at the same company still open in the pool are rejected as "colleague of someone who replied".
- **A negative reply or an opt-out:** the person and their company go into the exclusions.
- **Silence after message two:** nothing more. Two messages, never three. After **[10 business days]** the tracking row closes as `ended_no_reply`.

## Rules
- **One sender on the seat.** While this runs, no other system sends messages or invitations from that LinkedIn account. A sourcing tool that shares the seat sources, it does not send.
- **Nothing sends without a `send` decision on that exact text**: not a rewritten message, not a near-identical one, not a third message.
- **This process sends no invitations.** It only works on connections that are already accepted.
- **The inbox is the truth.** An existing thread with a message from your side closes the row, whatever its date and whatever the campaign files say.
- **Only a person lifts an exclusion.**
- **Idempotent by construction.** The URL key shared by the tables, the thread re-read before every send, the send dates and the terminal statuses make a second run on the same day a no-op.

## Output
A short report per run: connections added and their sources; rows closed as already contacted or excluded; verdicts; pairs queued; decisions collected, with their reasons and strengths; rows blocked by a pre-send check, naming the check; rows awaiting acceptance; messages one and two sent; replies received; rows waiting past the caps or on a provenance; and any thread that could not be read. Nothing is estimated: what was not done is reported as not done.