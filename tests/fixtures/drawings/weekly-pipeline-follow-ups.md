# Draft follow-up emails for deals that went silent

**When to use it**: every open deal in your CRM deserves a follow-up at some point, but the CRM's "last interaction" date can't tell you which ones, because it counts your own mass sends, newsletters and out-of-office replies. This measures the real silence in the mailbox, stays clear of accounts you already wrote to this week, and leaves one draft per account on its original thread, for a person to review and send. The process never sends anything and never changes a stage.

```
              Scheduled routine, once a week in the morning
              "Prepare this week's follow-up drafts on the open pipeline."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull the open pipeline                     │   attio_attribute
│  Every entry in your open stages, all owners,   │   attio_entry  sorted on the entry id
│  paged on a sort key no two entries share.      │   attio_record
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Screen the roster                          ║   attio_meeting
║  Only a future meeting, no email or a test      ║
║  record stops it, never a stale status note.    ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ filtered out       meeting set, no email, test record
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Measure the real silence in Gmail          │   gmail_message
│  Count from the prospect's own last message,    │   last interaction is a hint, not proof
│  searching the domain and the company name.     │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ too soon           the prospect wrote too recently
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Check your own last send                   ║   gmail_message in:sent
║  A recent message from your side defers the     ║
║  account to next week.                          ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ deferred           you already wrote this week
                         ▼  silent, and clear to write
┌─────────────────────────────────────────────────┐
│  5 · Find the angle                             │   gmail_message
│  The thread first, for its words and the open   │   attio_meeting transcript
│  objection; the last call only if it has none.  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Write the draft on the thread              │──▶  Gmail draft  a reply on the original thread, unsent
│  Give something first, then ask small: one      │──▶  Attio note  angle, silence, unanswered follow-ups
│  link, a short message.                         │   gmail_compose · attio_note
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no safe reply      every reply would copy an outsider
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Verify the drafts, then report             │   gmail_message drafts
│  List the drafts against what the run thinks    │   email_send
│  it made, then send the prioritized report.     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  8 · A human reviews and sends                  ║
║  A person reads and sends every draft, and      ║
║  alone moves any account to lost.               ║
╚═════════════════════════════════════════════════╝

▪ terminal: the account gets no draft this week, and the report says why
```

Steps 3 to 6 run per account. The run is split into batches of about a dozen accounts handed to sub-agents, each writing a per-account report to a file, so the orchestrating run never holds the message bodies in context. Step 5 reads the call transcript only when the thread gives no angle, because the transcript is the most expensive read in the process.

## 1. Pull the open pipeline
- <tool:attio_attribute> `target="lists"`, `identifier=[your pipeline list]`, `op="statuses"`, `attribute=[your stage attribute]`: read the stage values from the workspace rather than typing them from memory.
- <tool:attio_entry> `op="query"` on **[your pipeline list]**, with `filter` set to one `$or` over the open stage values just read, `{"$or": [{"[stage attribute]": "[open stage 1]"}, {"[stage attribute]": "[open stage 2]"}]}`, and no owner filter: every owner's accounts are in scope. A status attribute matches one value at a time, so several stages are combined with `$or`, never with a range or a list operator. If the workspace refuses the combined filter, run one query per open stage and merge the results on the entry id. **Pass `sorts` on a key no two entries share, the entry id, ascending (`[{"attribute": "entry_id", "direction": "asc"}]`), and page with `offset`.** A non-unique sort key produces overlaps and holes across pages, which is how an account silently drops out of a run.
- <tool:attio_record> `object="companies"`, `op="get"` on each entry's parent record, in parallel batches of **[ten]** or fewer: larger batches overflow the tool output.
- **One account, one draft.** Two entries pointing at the same company, or two company records for one real company, are treated as a single account. Note the duplication on each record with <tool:attio_note> `op="create"`, for a person to merge. Never merge from this process: a merge is irreversible and creates a third record.

## 2. Screen the roster
There are four reasons to write nothing, and only these four:
- **A meeting already booked in the future.** <tool:attio_meeting> `op="list"` with `ends_from=<now>`, paged by `cursor`, matched to the account on the meeting's linked company record or on a participant's email domain.
- **No reachable email anywhere**: not on the linked people, not in any thread. A bouncing address counts as no address.
- **A test record from your website form**: random-character notes, repeated dummy phone numbers, no company name, no domain and no exchange.
- **The prospect wrote less than [10 days] ago**, measured in step 3.

**Never screen on a free-text status or notes field.** It is often a year out of date. An account marked shelved, on hold or "building it in-house" still gets a draft, on a different part of your product.

## 3. Measure the real silence in Gmail
The CRM's last-interaction date is a preselection, not evidence. It counts newsletters, spam landing in a shared inbox, automatic out-of-office replies and, above all, your own mass follow-ups.
- <tool:gmail_message> `op="search"` with the account's domain as `query`, then a second search on the company name in quotes, because a real conversation often runs through a domain that is not attached to the record. Raise `max_results` above the default for a long history. Each result is one message carrying its `threadId`, `from` and `date`, newest first.
- **The silence counts from the last message the prospect actually wrote**: the newest result whose sender is on their side, not yours.
- **Count your consecutive messages since then**, on that thread. Calendar invitations and automatic acknowledgements do not count. **[Four]** or more follow-ups with no reply at all make the account a **lost candidate**, reported with the count and the date of the prospect's last message. A prospect who answered at all, however briefly, resets the count to zero: what matters is total silence, not slowness. The draft is still written, on a genuinely new angle, and the stage is never changed by the process.

## 4. Check your own last send
- <tool:gmail_message> `op="search"` with `in:sent newer_than:[7]d` and every domain and address step 3 tied to the account, joined with `OR`: the record's domain, the other domain the conversation actually runs through, and any personal address found on the thread. Checking the record's domain alone misses a recent send to that other domain. If anyone on your side wrote to this prospect in the last **[7 days]**, the account is deferred to next week and no draft is created. Two follow-ups five days apart read as pressure, and a mailbox full of drafts nobody will send is worse than an empty one.
- **This check runs before any writing, not after.** A batch that is written first and purged afterwards throws away the writing effort on every account that collided with a recent send.
- The check only sees what left this mailbox. If some of your sequences send from another tool, read that tool's send log for the same window too.

## 5. Find the angle
- <tool:gmail_message> `op="get"` on the last few messages of the thread: the prospect's real language, the register (formal or first-name terms), and the objection left hanging.
- <tool:attio_meeting> to find the account's last call, **only** when the thread gives no angle. Meetings cannot be filtered by record, so list past meetings once for the whole run, before the batches: `op="list"`, `starts_before=<now>`, `sort="start_desc"`, paged by `cursor`, keeping the newest meeting matched to each account the same way as in step 2 (linked company record, or a participant's email domain). Then `op="recordings"` with that `meeting_id`, and `op="transcript"` with the returned `call_recording_id`.
- **Check whether they already have access to your product.** Accounts that went through a demo or a trial often do. A user id from your product on the record, or any exchange about getting started, settles it. Talk about the access they have, and never offer a trial to someone who already has one: it reads as not remembering them.

## 6. Write the draft on the thread

### Tone, the part that is easiest to get wrong
A follow-up proposes. It never reproaches and never demands, because a prospect's silence is not a debt owed to you. Three families of sentence are forbidden without exception:
- **Reproach.** "All my follow-ups went unanswered", "we wrote a while back and heard nothing since". Never mention how long the silence has lasted.
- **Summons.** "Is this on the agenda, yes or no?", "should we close the subject?", "without an answer I'll assume", "one word will do". Demanding a verdict or imposing a reply format is aggressive however politely it is wrapped.
- **Commentary on yourself.** "I'd rather ask frankly than keep writing", "I'm reaching out one last time". The prospect does not need your feelings about your own prospecting.

The test for every sentence: would you say it out loud, as written, to a client you respect and want to see again?

### Opening
Open on a human line only when it is anchored in something real: the time of year, a public event at the account (a raise, a new site, an appointment, a trade show), or how the relationship started. Match the language and register of the thread. An unanchored "hope you're well" is empty: either the opening is anchored or the message goes straight to the point. Never invent an event, and never open on a personal note with someone you have never spoken to.

### Give before asking
Every follow-up carries something the reader did not have before opening it, and the ask that follows stays small and unconditional. When the history offers no hook, build the value from your product and the account's trade. One is always available:
- A way to try it on their own data, alone, or in a short guided session.
- A figure computed from their own situation, when your product can produce one.
- A capability that answers the objection left open: security, integration, volume, language.
- A result at a comparable company in their sector or size, unnamed unless the case is public.
- A product change that did not exist at the last exchange.

After an explicit refusal, do not reopen the refused subject. Open on another part of the product without revisiting their decision.

### The ask must not create manual work at scale
Across dozens of accounts, every "send it over and I'll set it up for you" that gets a yes becomes hours of manual work. Hand control back to the prospect, or go through a short meeting.

| Instead of | Write |
|---|---|
| send me a sample and I'll run it for you | try it on a sample of your own data, in the account you already have |
| tell me how you work and I'll configure it for you | twenty minutes together and we configure it on your own data |

The exception is deliberate and rare: an opportunity worth that time (a large account, a stated high volume, an identified decision maker, an advanced exchange). There, offer a bounded, prepared gesture, never an open invitation to send material, and flag the account in the report.

### Links, form and claims
- **One link per message**, as an anchor on a few words of a sentence, never a bare URL, never in the signature. **[Your product's sign-in link]** when the sentence is about their account; **[your booking link]** when the call to action offers a conversation. Never both: two links are two requests, and the reader follows neither. No link at all when the call to action offers neither.
- The language and register of the thread, **[90 to 140]** words including the opening, one call to action, no invented fact. **No em dash and no en dash**, checked as a string: they read as machine-written.
- **[The claims you must never make]** stay listed in this process and are checked on every draft.

### The call
- **Pick the message to reply to by who the reply will reach.** Once `reply_to` is set, <tool:gmail_compose> ignores `to` and `subject`: the recipients come from the message replied to. A message counts as your own only when it was sent from this mailbox. A reply to a message sent from this mailbox goes to everyone on that message's To line, a former colleague included. A reply to any other message goes to its sender, or its Reply-To, so a reply to a teammate's or a former colleague's message sent from their own address goes back to them. So take the last message on the thread that is not a draft. If it is the prospect's, reply to it. If it was sent from this mailbox with only the prospect's side on its To line, reply to it. If its To line holds anyone else, or it came from another address on your side, reply to the prospect's own last message on that thread instead. If the thread holds no message from the prospect's side to fall back on, write no draft, and list the account in the report for a person to handle by hand.
- <tool:gmail_compose> with `reply_to` set to that message id, `cc` set to **[the teammate copied on every follow-up]**, the message in `html`, the same message as plain text in `body` (the tool requires `body` even when `html` is passed), and `mode` left at its default, which saves a draft. **Never `mode="send"`.** Read `kind` in every response: it must be `"draft"`.
- The message goes in `html` so the anchor is a real hyperlink: one wrapping `<div dir="auto">`, paragraphs separated by `<br><br>`, the signature on the last line, no style, no class, no html or body tag.
- The original cc is not carried over to a reply, which is why `cc` is always passed explicitly.
- **Check the draft landed on the thread you aimed at.** The compose response documents `kind` and the message ids; when it carries no thread id, <tool:gmail_message> `op="get"` on the returned message id gives the `threadId`. A follow-up that lands as a new conversation loses the history the prospect needs to place you.
- **There is no edit.** To correct a draft, create a new one with <tool:gmail_compose>, then <tool:gmail_message> `op="trash"` on the old draft's own message id: one message, never a whole thread's messages.
- **Copies are a closed list.** Only **[your sender]** and **[the teammate in cc]** ever appear on outgoing prospect email from your side, and nobody outside the prospect's side is ever on the To line. Former colleagues still sitting on the original thread are never copied and never presented as a current contact. Citing what a former colleague did or sent is fine, as long as you take the commitment over in your own name.
- <tool:attio_note> `op="create"` on the company record: the subject as `title`, and in `content` the angle, the date of the prospect's last message, the count of consecutive unanswered follow-ups, and the draft body in plain text.

## 7. Verify the drafts, then report
- <tool:gmail_message> `op="drafts"` once for the whole run, with `max_results` above the total number of drafts the run believes it created across all batches, plus a margin for drafts that were already in the mailbox: the listing covers the whole mailbox, newest first, so a limit sized to one batch cannot confirm the rest. Match the listing on the message ids the compose responses returned, never on a count: a draft reported as created can still be missing, and a direct listing is the only way to see it. Read each draft's `to` while you are there. A draft addressed to anyone outside the prospect's side is recreated as a reply to the prospect's own last message on that thread, which goes to its sender, and the old draft is then trashed on its own message id. When the thread holds no message from the prospect's side, the old draft is trashed, no draft is left, and the account goes into the report for a person to handle.
- <tool:email_send> the report to **[whoever sends the drafts]**. A scheduled run reaches nobody otherwise.

## 8. A human reviews and sends
A person reads every draft, edits it if needed, and sends it. That person alone moves an account to lost, or changes any stage.

## What this never does
- Never sends an email, and never calls <tool:gmail_compose> with `mode="send"`.
- Never changes a stage, never merges or deletes a record.
- Never screens an account out on a free-text status field.

## Output
One report per run, in this order: the drafts created, in priority order; the lost candidates with their counts of unanswered follow-ups; the accounts filtered out, grouped by reason, with test records counted as one block; the accounts whose hook came from the product rather than the history, which are the ones to reread first; the accounts offered a prepared personal gesture; the accounts left without a draft because every possible reply would have reached someone outside the prospect's side; gaps between the CRM and what the threads actually show; and the accounts that deserve a phone call on top of the email.