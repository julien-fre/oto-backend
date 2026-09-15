# Draft replies to customer support messages in Slack

**When to use it**: customers write to you in shared Slack channels and a support inbox and want a same-day reply that is accurate about the product, written in their language and sounding like the person who sends it. Several times a day this reads what is new, splits each message into its tickets, reproduces a bug claim before any draft agrees with it, checks the tracker for an existing issue, and lays the drafts out in your internal channel. A person sends; the run never writes in a customer channel or sends from the inbox.

```
              Scheduled routine, six times a day on weekdays
              "Draft replies to what came in on the customer channels this morning."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read what is new since each mark           │   data_rows  the roster, a person's list
│  Rostered channels and the inbox, threads       │   slack_read_history, slack_read_thread
│  opened, own messages dropped.                  │   gmail_message
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing new       the mark has not moved
                         ▼  at least one new message
┌─────────────────────────────────────────────────┐
│  2 · Split each message into tickets            │
│  A bug and a pricing question in one message    │
│  are two tickets, each typed and quoted.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not a ticket      scheduling, thanks, chit-chat
                         ▼  one pass per ticket
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  3 · Check the standing answers  │  │  4 · Reproduce a bug claim       │   only on a factual claim
│  A reply already sent for this   │  │  On their account, or the docs,  │
│  question is reused as written.  │  │  before a draft agrees with it.  │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     ├──────────▶   ▪ claim did not hold   no denial, raised live
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Draft in the owner's voice                 │   the senders table is binding
│  The customer's language, one line for a bug,   │
│  most of the words on the commercial question.  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Log, file, then move the mark              │──▶  tickets table  one row per ticket, keyed to its message
│  Write the ticket, match it in the tracker      │──▶  Linear  a comment on a match, else a Backlog issue
│  before creating, then advance the mark.        │──▶  roster  the mark, only after the rows land
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing to send   no draft, no channel change
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Post the digest, drafts in thread          │──▶  Slack channel  internal only, under 900 characters
│  One line per ticket under a hard cap; every    │──▶  Slack thread  each draft, then the internal findings
│  draft and finding in the thread.               │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · A human sends                              │   the one human step
│  The owner edits or sends from the thread; the  │
│  run never posts in a customer channel.         │
└─────────────────────────────────────────────────┘

▪ terminal — nothing further is drafted there
```

Steps 2 to 5 run once per ticket, and step 6 logs and files each ticket before moving its surface's mark. Step 3 runs on every ticket; step 4 only on a ticket that makes a factual claim about the product, and it is never skipped on a bug, because agreeing to something that is not happening is the one mistake this process cannot take back. A ticket whose claim did not hold at step 4 still gets its row and its reply in the thread, with no draft; a claim that could not be checked falls back to the documentation rather than exiting. A failure before step 6 leaves the mark where it was, so the next sweep re-reads those messages: re-drafting a ticket is cheap, losing one silently is not.

## Before the first run: the tables a person keeps
- **The roster**, one row per surface: source (Slack or email), the channel id or mailbox, customer, owner, language, `active`, `last_ts` (the newest message already handled) and notes. A person adds a row; the run never does. Channel discovery is deliberately not used: listing channels usually returns public channels only, and a private channel shared with a customer is invisible to it.
- **The senders**, one row per owner: greeting, sign-off, languages, voice notes and one reference reply they actually sent.
- **The standing answers**: pattern, answer, language, owner, last used. It starts small and grows from what people send (step 8).
- **The tracker team**: the id of the Linear team new issues are filed under, resolved once with <tool:linear_team> `op="list"` and kept on a settings row beside the roster (or as a column on each roster row when customers map to different teams).

## 1. Read what is new since each mark
- <tool:data_rows> on the roster, `filter={"active": true}`.
- **A row with no `last_ts` is a new surface.** Read only its latest message, set the mark to it, and draft nothing. A sweep that meets a channel for the first time must not answer weeks of backlog.
- <tool:slack_read_history> per channel with `oldest` = the mark and `limit=100`, following `cursor` to the end of the window. It returns top-level messages only.
- <tool:slack_read_thread> with the parent's `ts` for every message whose `reply_count` is above zero. Judging a thread from its parent is how answered questions get reported as unanswered.
- **A new reply in an older thread never shows up past the mark**, because its parent predates the mark. Re-read the top-level messages of the last **[7 days]** and open every parent whose `latest_reply` is newer than the mark.
- <tool:gmail_message> `op="search"` with `query="in:inbox after:<the mark in epoch seconds> -from:noreply -from:no-reply"` and `max_results=100`, then `op="get"` per message. There is no cursor and the default is 20, so a busy inbox silently loses its oldest messages: when a page comes back full, search again with `before:<the oldest returned message, in epoch seconds>` added, until a page comes back short. `after:` with a calendar date is day-granular; with epoch seconds it is not. Dedupe on message id, because boundary messages come back. Skip the auto-replies the query did not exclude.
- Drop your own app's messages and your team's, by user id in Slack and by your domain in the inbox. Neither is a ticket.
- **A surface that cannot be read is recorded, not narrated.** Set `active` to false with the reason and the date in notes. It reaches the post only on the sweep where its state changed (it broke, or it came back), never on every sweep while it stays broken.

## 2. Split each message into tickets
- One message can carry several tickets, and mixing them produces a reply that answers the small question and misses the large one. Type each one: **bug**, **question**, **how-to**, **positioning**, **billing** or **feature request**.
- The split that matters most is **bug against positioning**. A message that reports a defect and, in the same breath, asks what the product is for has raised both, and they have different owners, different fates and different shares of the reply.
- Keep the customer's own words on every ticket. Scheduling, thanks and chit-chat are not tickets and get no row.

## 3. Check the standing answers
- <tool:data_rows> on the standing answers table, `filter={"language": "<the ticket's language>"}`, with one `q` per key noun from the customer's words. `q` is a single substring match across the row, so several words in one `q` rarely match anything, and a missed standing answer gets rewritten. When the language-filtered table is small, read it whole and match on meaning instead.
- **A standing answer is sendable as written.** Change the name, adjust what this customer's situation genuinely changes, and stop. Rewriting an answer someone already sent is how the voice drifts.
- A question with no standing answer is a candidate for one. It goes in the internal findings reply at step 7, not in the top-level post.
- Facts about the product (limits, plans, what is supported) come from your own documentation pages, <tool:oto_doc> `op="search"` then `op="get"`, before a draft states them. A commercial term (service levels, contract or cancellation terms, anything priced) that the published record does not state is never added; the draft says a person will confirm it.

## 4. Reproduce a bug claim
Only on tickets carrying a factual claim about the product, and always on a bug.
- Reproduce it **on the customer's own account** through **[your product's admin API or read connector]**, the access your support team already has, and **compare with a healthy account of your own**. Wrong everywhere and wrong only for them are different bugs, with different fixes.
- Record one of three results on the ticket, never collapsed: **reproduced**, **not reproduced** (checked, and it behaves correctly on their account), or **could not check** (no access to that account, or the surface did not answer).
- **What the check is for**: it decides whether the draft can agree. It does not decide what the reply says. Everything it finds stays internal, in the ticket row and the thread, never in the draft and never in the top-level post.
- **Not reproduced: never draft a denial.** Draft nothing on that ticket, say in the thread what was tried, and let a person raise it live. A customer who reports something real and is told in writing that it does not happen stops reporting.
- **Could not check is not "not reproduced".** Fall back to the documentation check from step 3. When the record contradicts the claim, it leaves like a claim that did not reproduce: no draft, the page named in the thread. When the record neither confirms nor contradicts it, the draft agrees in the customer's own words, because a missing page proves nothing. An agent with no reach into customer accounts would otherwise draft nothing on every bug.

## 5. Draft in the owner's voice
- Load the owner's row from the senders table and match its reference reply. This step only names what people get wrong.
- **The customer's language**: the one they wrote in, the roster's language when that is ambiguous. Write the draft in it; do not translate one.
- **A bug gets one line**: agree in the customer's own plain words, then say a fix is being worked on. No field or internal system names, no counts, no diagnosis, no workaround, no date.
- **The commercial question gets most of the words**, aimed at how the customer makes money rather than at what they happen to be doing this week.
- Plain prose: no backticks, no bold, no tool names, nothing the record does not back.

## 6. Log, file, then move the mark
- <tool:data_write> one row per ticket, keyed `<surface>:<message ts or id>:<n>` so a rerun updates rows instead of doubling them: surface, customer, author, language, owner, type, the ask, the quote, the standing answer used, the reproduction result, the draft, status (`drafted` or `no draft`), product signal, tracker reference and Slack `ts`. The **product signal** column is where a positioning gap or a cluster of defects is recorded; it reaches Slack only if it changes what someone should do before sending.
- **Bugs and feature requests are matched in the tracker before anything is created**, and only when the claim held:
  - <tool:linear_issue> `op="search"` in two or three phrasings: the customer's words, the symptom, your team's name for it. A plain-language complaint and a technical issue title are often the same issue.
  - A match: <tool:linear_comment> `op="create"` on that issue, with the customer, the date, the quote and the surface.
  - No match: <tool:linear_team> `op="states"` on the tracker team's `team_id` for the Backlog state id, and <tool:linear_label> `op="list"` for the bug or feature label, both resolved fresh every run, then `op="create"` on the issue tool with that `team_id` and a title that describes the symptom rather than a guessed cause, in Backlog, priority left at zero. Priority and state are a person's call.
  - **A row that already carries a tracker reference is never filed again.** Write the row, file, then write the reference onto the row: a sweep that failed after the reference landed but before the mark moved re-reads the message on the next run without opening a second issue. The search-first rule covers the narrow gap between filing and writing the reference, because the rerun finds the issue it just created.
  - If the tracker does not answer, the draft still goes out for review, with the reference left empty and the reason on the row.
  - Any tracker with search, comment and create works the same way; it is written here for Linear.
- **Then, and only then, advance `last_ts`** on that surface's roster row with <tool:data_write>, to the newest message read, and on the inbox only once its search was paged until a page came back short. A mark moved before its rows landed is a lost ticket.

## 7. Post the digest, drafts in thread
**Quiet is silence, and it is a test.** A sweep posts when it has at least one drafted ticket or a surface whose state changed this sweep. Nothing else is a reason to post: not a finding, not confirmation that the sweep ran. Most sweeps on an ordinary day post nothing.

- <tool:slack_post_message> to **[your internal support channel]**, **at most 12 lines and 900 characters as a reader sees them**, a link counting as the word it displays. No code block, no verbatim customer quote, no tool name.

```text
*Support sweep · <day> <time>* · <n> surfaces read · <n> to send

*1. <Customer> / <first name>* · <the ask, ten words or fewer> · bug, reproduced · <language>
*2. <Customer> / <first name>* · <the ask> · positioning · <language>

*Check 2*: the draft leans on a limit the pricing page does not state.
```

- **The header line** carries the date, the time, the surfaces read and the drafts waiting. Nothing else.
- **One line per drafted ticket**, numbered in the order the thread replies will follow: customer, first name, the ask in your own words in **ten words or fewer**, the type, the language. It is a label that tells two tickets apart, never a quote. A posted message cannot be edited afterwards, so the top-level post cannot link to replies that come after it; the numbers do that job.
- **A check line only for something that would make someone send a wrong draft**: twenty words, naming the ticket number. A claim that did not reproduce, a draft that promises what the record does not back, a standing answer that disagrees with the draft.
- **One line for a surface whose state changed** this sweep, and only then.
- Approaching the cap almost always means material that belongs in the thread, so move it there first. If the post still will not fit, cut the surface line, then every check line after the first, then labels down to five words. Never cut a ticket line.
- **The thread**, replies on the post's `ts`: one per numbered ticket, in order, opening with its number and label, at most two lines of context, then the draft in a code block so it copies clean. A ticket with no draft gets a reply saying why. Then one internal findings reply, only if there is something to put in it, at most 1,200 characters: what reproduction found, standing-answer candidates, product signals, tracker references. Write the post's `ts` onto every ticket row.
- **What never reaches the channel**: the run's account of itself. Tool behavior, mark mechanics, table keys, and corrections to what an earlier sweep said about any of those belong in the run log, not in a channel meant for customer replies.

## 8. A human sends
The owner reads the draft in the thread, edits it or not, and sends it from the customer's channel or from the inbox. **The edit is the deliverable.** Write what was actually sent into the standing answers table with <tool:data_write> and set the ticket row to `sent`. A rule the edit reveals goes into that owner's voice notes, which is how the next draft needs less editing.

## Output
Per sweep: surfaces read, surfaces whose state changed, tickets by type, drafts waiting, tickets with no draft and why, issues commented and created, and marks advanced.