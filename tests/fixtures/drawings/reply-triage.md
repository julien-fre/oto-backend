# Classify Lemlist replies and draft responses

**When to use it**: you run cold email campaigns and replies land in an inbox nobody watches continuously, so a yes that arrives at nine gets read after lunch and a "not now, try me in the spring" gets forgotten. This polls the replies every fifteen minutes, sorts each one into a closed set of classes, drafts an answer for the ones worth answering, and posts them to a Slack channel for a person to send. It never answers a prospect itself. The one write it makes to a sending system is honoring an unsubscribe.

```
              Scheduled routine, every 15 minutes on weekdays in working hours
              "Catch up on the campaign replies since this morning and post them for review."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the key and fix the window           ║   lemlist_team
║  Prove the Lemlist key answers, then read the   ║   data_rows
║  window, the cap and the channel from config.   ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ alert posted      the key or the config did not answer
                         ▼  the key answers
┌─────────────────────────────────────────────────┐
│  1 · Poll the replies                           │   lemlist_get_activities
│  Page newest first and keep only what arrived   │
│  inside the window, filtered on its own clock.  │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ quiet tick        no reply inside the window
                         ▼  at least one reply
┌─────────────────────────────────────────────────┐
│  2 · Drop what the ledger holds                 │   data_rows
│  A reply key already on a row was handled by    │
│  an earlier, deliberately overlapping tick.     │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already handled   its reply key is on a row
                         ▼  one pass per new reply
┌─────────────────────────────────────────────────┐
│  3 · Strip the quote, recover the person        │   lemlist_inbox, lemlist_lead
│  Cut the quoted pitch, then read who they are   │   hubspot_object, read only
│  and who owns them.                             │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Classify against a closed rubric           ║   an auto-reply is never positive
║  Positive, ambiguous, negative, out of office,  ║
║  unsubscribe or other.                          ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ counted only      negative, out of office or other
                         ▼  positive, ambiguous or unsubscribe
┌─────────────────────────────────────────────────┐
│  5 · Draft the answer                           │   claims only from citable proof points
│  A capped draft in the prospect's language and  │
│  the sender's voice; unsubscribes pass through. │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Act, write the rows, then post             │──▶  Lemlist  the unsubscribe, the only write that leaves
│  The unsubscribe first, the ledger second,      │──▶  Slack  one line per reply, drafts in the thread
│  the post last, its timestamp right after.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · A person sends                             │   the one human step
│  The rep edits the draft or ignores it; the     │   slack_read_thread
│  next tick reads the thread to close the row.   │
└─────────────────────────────────────────────────┘

▪ terminal — the tick or the reply goes no further; a reply stopped at the rubric is still logged
```

## The rules that govern every tick
- **Nothing is sent to a prospect.** `lemlist_inbox_send`, `lemlist_launch_lead`, `lemlist_campaign_start` and `lemlist_campaign_auto_review` are never called and never enabled. The draft lives in Slack and in the ledger, and a person sends it from their own mailbox.
- **The one outward write is the unsubscribe**, made in the tick that recognized it and never left for a human to remember.
- **The CRM is read, never written.** A classification and a draft are not facts about the deal; the note belongs to whoever actually answers.
- **A quiet inbox and a broken connector never look alike.** A tick that reached Lemlist and found nothing posts nothing. A tick that could not reach it posts an alert. Silence can then be trusted to mean "no replies".

## 0. Check the key and fix the window
- <tool:lemlist_team> with `op="team"`: a free read that proves the key answers before anything else runs. If you run several Lemlist workspaces, each has its own key and its own connector instance: pass the instance on every Lemlist call, poll them one after the other, and let a key that fails on one skip that workspace (named in the run's close) without stopping the others. Never fall back to another workspace's key.
- <tool:data_rows> on your config table: the Slack channel id, the **[window]** in minutes (thirty on a fifteen-minute cadence), the **[draft word cap]** (eighty works), and one row per sender keyed on their sending email, lowercased: display name, default language, sign-off and voice notes, plus a default row for a sender the table does not know. A required row that is missing blocks the tick. Never fall through to a partial poll: a half-read window written into the ledger becomes a permanent hole, because the next tick treats those keys as handled.
- **The window overlaps the cadence on purpose.** Thirty minutes polled every fifteen means a single failed tick loses nothing. The price is that about half of every poll is a reply an earlier tick already handled, and step 2 is what absorbs that.
- **Working hours are local; cron runs in UTC.** A fixed expression is wrong for half the year once daylight saving shifts. Register a schedule wide enough to cover both offsets, and have this step refuse a tick whose local time falls outside **[working hours]**: no read, no write, no post, closed with zero counts.
- <tool:data_rows> on the ledger with `limit=1`: an empty ledger means this is the first tick ever. **The first tick is a dry run over twenty-four hours.** It classifies, drafts and writes the ledger, and posts nothing, so the first live tick does not post a day of replies at once.
- **Exit ▪ alert posted**: the key or a required config row did not answer. Post the alert (step 6) when the channel id is known, close the run as blocked naming what was absent, and let the next tick cover the same window.

## 1. Poll the replies
- <tool:lemlist_get_activities> with `activity_type="emailsReplied"`, `limit=100` and a rising `offset`. **The endpoint ignores its date parameters**: `start_date`, `end_date`, `min_date` and `max_date` are accepted and discarded. Filter on each activity's own `createdAt` against now minus the window, computed from the clock at run time, and stop paging only once a page's oldest `createdAt` falls before the window start.
- Don't reach for `all_pages` with `since` to get a server-side date floor. That route ignores the other filters, so it returns opens, clicks and sends too, and pages through far more than the window.
- **Never filter by campaign status.** Pausing a campaign stops the sequence going out, not the answers coming back, and a finished wave is usually paused. <tool:lemlist_get_campaign> is read to name the campaign in the post, never to decide whether a reply counts.
- Keep per activity: the activity id, `createdAt`, `campaignId` and `campaignName`, `leadId`, the contact id when present, the lead's email, name and company, `sendUserEmail` and `sendUserName`, and any field that looks like a body or a subject.
- **Exit ▪ quiet tick**: no activity inside the window. Write nothing, post nothing. This is the common case, and it has to stay silent for the alert to keep its meaning.

## 2. Drop what the ledger holds
- **The key is `reply_key` = the lead's email lowercased + `|` + the campaign id**, deliberately not the activity id. A prospect who answers twice in the same conversation updates one row (excerpt, class, draft) instead of creating a second, so the reader sees one line per conversation, not one per message.
- <tool:data_rows> on the ledger, filtered to the keys built from step 1. The ledger is the duplicate defense, not the query; nothing about the fetch prevents repeats, and nothing should.
- Two cases go on rather than exiting. A row marked `posted` with an empty `post_ts` and `dry_run` false was classified by a tick that failed to post: carry it to step 6 without reclassifying it. A row whose stored reply is older than the activity in hand is the same person writing again: carry it through steps 3 to 5 as an update.
- **Exit ▪ already handled**: the key is on a row that needs no post (it is stamped, it was never going to get a line, or it came from the seeding dry run), and the activity is not newer than the row.

## 3. Strip the quote, recover the person
- **The body field is not documented.** Probe the candidate fields on the activity first. Then <tool:lemlist_inbox> with `op="messages"` and the contact id: leave `mark_as_read` unset, because on that op it changes the conversation's state on what should be a read. Then <tool:lemlist_lead> with `op="get"` and the lead id as the last fallback. On a miss, log the activity's keys so the field can be found. A reply whose body is found nowhere gets no row, counts as an error, and is retried by the next overlapping tick while it is still inside the window.
- A contact (`ctc_…`) is the person in Lemlist's own CRM; a lead (`lea_…`) is that person inside one campaign. The inbox and the do-not-contact flag address the contact; sending state belongs to the lead.
- **Strip the quote before anything reads the body.** Most mail clients quote the message being answered, so a three-word "no thanks" carries the whole pitch, its call to action and its sign-off, and a classifier reading the raw body classifies the pitch. Cut from the first quote marker onward: lines starting with `>`, "On … wrote:" and its equivalents in the prospect's language ("Le … a écrit :"), "-----Original Message-----", a From/Sent header block, and the underscore rule Outlook inserts. Keep the signature: it carries the language and often the real job title.
- **Then recover the person.** Your outbound table first, by email, if you keep one: who they are, which campaign or signal produced them, what they were sent, and which rep owns them. <tool:hubspot_object> with `op="search"`, `object_type="contacts"` and a filter on the email, read only, when that table has no row. A reply from a stranger is still classified, drafted and posted, marked as not found; no row is invented for them.

## 4. Classify against a closed rubric
One judgment per reply, on the stripped body: `{class, confidence, reasoning, language}`, with confidence between 0 and 1 and reasoning in one sentence. Six classes, no seventh; anything that fits none is `other`, with the reason named.
- **positive**: wants a meeting, a demo, a document, or asks for times. Drafted and posted.
- **ambiguous**: neither yes nor no. A question, "not now, try me next quarter", "wrong person, ask my colleague", "we already use a tool, how is this different?". Drafted and posted.
- **negative**: a refusal of the offer. Counted.
- **out of office**: an absence, which says nothing about interest. The row stays open, the return date is parsed into its notes so it can be found again, and nothing else is written for it. Treating it as a refusal loses the prospect twice.
- **unsubscribe**: "remove me", "stop emailing me". Acted on in this tick.
- **other**: a bounce, an auto-acknowledgement, a signature-only reply. Counted.

The two confusions that cost the most: **negative against unsubscribe**. A refusal is an opinion about the offer, and the company can be approached again later; a request to stop is an instruction. When a sentence carries both, it is an unsubscribe. And **out of office against positive**: an auto-reply that names a colleague reads like a warm redirect, but an automatic message is never positive. The colleague goes into the notes for a person to decide about.

- **Exit ▪ counted only**: negative, out of office and other. The row is still written in step 6 with its class and excerpt; it just never gets a line of its own in the post.

## 5. Draft the answer
Only for positive and ambiguous replies. An unsubscribe passes through untouched so step 6 can act on it.
- **The sender's voice.** `sendUserEmail` lowercased is the key into the sender rows: name, sign-off, and voice notes injected verbatim. No row means the default row, flagged as an unmapped sender in the post and the run's close, so a person adds the missing row. The run never adds one itself.
- **The prospect's language**, as classified; when it could not be determined, the sender's default language.
- **At most [draft word cap] words, counted before posting.** Open on the first name alone; close on the sender's own sign-off. The cap is a ceiling, not a target: two lines that say yes to a slot beat a paragraph.
- **Answer the question that was asked, make one ask (a slot, a document, the right person), and stop.** A draft that answers a question with a pitch is worse than no draft: the rep has to unpick it before sending.
- **Every claim comes from your knowledge base, never from memory.** A figure appears only if a row in your proof-points table carries it, marked citable, and in that row's exact wording. What shipped comes from your changelog, never your roadmap. If the reply names a competitor, what the draft says about it comes from your battlecard and nothing beyond it. No price unless your pricing rule allows one in writing.
- **The register is plain prose**: no bold, no bullet list inside an eighty-word email, no tool or internal system name, and nothing that reveals the draft was machine-written.

## 6. Act, write the rows, then post
The order is the mechanism.
- **First, the unsubscribes**: the only obligation a later failure must not drop. <tool:lemlist_unsubscribe> is three registers that do not talk to each other: `op="add"` is the email-and-domain list, `op="var_add"` the value list, `op="contact_add"` the do-not-contact flag on the CRM contact. Writing one does not write the others. Use `contact_add` with the contact id when the activity carries one, since it rides the person rather than one of their addresses; fall back to `add` with the email when it does not.
- **Second, the ledger.** <tool:data_write>, one row per reply, keyed on `reply_key`: prospect, email, company, campaign, received at, class, confidence and reasoning, language, owning rep, the stripped excerpt, the draft, sender mapped or not, a status (`posted` for a reply that gets a line, `no action` for one that doesn't, `new` for an out of office), `dry_run` true or false, and an empty `post_ts`. If you keep an outbound table, move the person's stage to replied and record the sentiment there too. Write an exclusion only on an explicit refusal or an unsubscribe, never on an ambiguous "not now". Rows first means a Slack failure after classification costs the post, not the classification.
- **Third, the post.** <tool:slack_post_message> to the configured channel, at most twelve lines. A header with the time, the number of new replies and the positive and ambiguous counts. One numbered line per positive or ambiguous reply: prospect, company, class, language, campaign, owning rep, "draft in thread", plus "unmapped sender" or "not in the outbound table" when true. One last line collapsing the rest to counts, with the return date on an out of office and "acted on" on an unsubscribe. If it will not fit, a second post carries the remainder; a line is never cut in half.
- **The thread**, through `thread_ts` on the first post's `ts`: one reply per numbered line, opening with its number, the prospect's own words in at most two lines, then the draft in a code block so it copies without Slack's formatting. Above roughly four thousand characters Slack splits a message and the response reports it in `split_into`: read that before concluding a post was cut, because a message can be deleted but not edited, and a re-post is a duplicate.
- **Fourth, immediately, the stamp.** A second <tool:data_write> puts `post_ts` on every row that got a line. A post without its stamp is read by the rep and then posted again fifteen minutes later.
- The app has to be a member of the channel or the post fails with `not_in_channel`, and a private channel is addressed by id. A person fixes both once.
- The alert, when step 0 exits, is one post with no thread: what failed, that nothing was classified this tick, and that the next tick covers the same window.
- In a dry run the step ends after the ledger: nothing is posted and `post_ts` stays empty. Close the run with `run_finish` and its `outcome` (there is no `status` parameter: a close that passes one is refused and the run stays open, which on a fifteen-minute cadence means dozens of orphaned runs a day), with the counts: fetched, dropped, per class, errors, posted, drafts, unmapped senders.

## 7. A person sends
- The rep reads the line, opens the thread, edits the draft or ignores it, and answers from their own mailbox. Nothing in this process sends.
- **The loop closes on the next tick, before polling.** <tool:slack_read_thread> on every row still marked `posted` whose `post_ts` is more than a few hours old: a human reply in the thread moves the row to `answered`. A row still `posted` after a working day is a positive reply nobody has dealt with. That is the number this process exists to drive down, so put it in the next post's header.
- What the edits teach goes back into the config, not into Slack: a greeting the rep always deletes or a claim they never make goes into their voice notes.

## Output
Per tick: replies fetched and dropped as already handled, counts per class, drafts written, unsubscribes acted on, rows posted and stamped, errors (bodies not found, judgments that failed to parse), unmapped senders, and the positive replies still waiting on a person after a working day.