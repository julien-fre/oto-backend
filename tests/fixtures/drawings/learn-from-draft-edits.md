# Learn your writing style from how you edit drafts

**When to use it**: an agent drafts your outbound messages (emails, LinkedIn notes) into a table, and you rewrite some of them before they go out. Each rewrite says something about how you write, and without a process it teaches nothing: the next batch of drafts makes the same mistakes. This reads your edits every day, puts your wording where the send reads it, turns a trait you correct twice into a standing rule, applies that rule to every message still waiting, and rebuilds any draft the change made stale. It never sends anything.

```
              Scheduled routine, daily, an hour after drafting
              "Catch up on the edits I made to this week's drafts and learn from them."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Reconcile, then find the edited rows       │   data_rows
│  An edit not yet applied replaces the message   │   data_write  pinned to the row's revision
│  at every status but sent, before any learning. │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Diff each edit against the draft           │
│  Every difference named in the person's words,  │
│  with the proofread's own fixes subtracted.     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Sort each difference                       ║   unsure means fact
║  A style trait generalizes. A name, date or     ║
║  fact fixed is logged and never becomes a rule. ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Count the sighting                         │   oto_doc  the sightings log
│  Style traits only: seen on a second row, or    │
│  stated in words, it is promoted to a rule.     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Write the rule where it belongs            │   oto_doc
│  A promoted trait goes to the playbook, the     │   oto_guide
│  general writing rules and the person's note.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Recalibrate the drafted messages           │   data_write
│  After a new rule: drafted rows only, edited    │   approved rows are named, never rewritten
│  ones on their own text, only what it touches.  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Rebuild the drafts a change made stale     │   gmail_compose  draft mode only
│  Any Gmail draft built from older text is       │   gmail_message
│  rebuilt and the superseded one removed.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Stamp what was sent, then report           │   gmail_message
│  Sent mail and LinkedIn threads mark rows sent, │   linkedin_unipile_chat
│  and every change they did not make is named.   │
└─────────────────────────────────────────────────┘
```

Steps 2 to 4 run once per edited row, and steps 5 and 6 only when a style trait reached a rule. A fact correction leaves the loop at step 3, but its row still goes through steps 7 and 8. Step 1's reconciliation, step 7 and step 8 run every day, whether or not anything new was learned.

**The table, and who writes each column.** One row per message. The whole design rests on keeping these writers apart.

| Column | Who writes it | What it holds |
|---|---|---|
| `drafted_version` | an agent, when drafting or recalibrating | the message as the agent wrote it; read-only for everyone else, the person included |
| `your_edit` | the person, by hand, or an assistant reworking a message with them elsewhere | their rewrite, whole; empty means the draft was fine |
| `message` | this run, your drafting process's proofread, and whoever reworks a message with the person | what actually goes out |
| `applied_edit` | whoever copies an edit into `message` | the exact `your_edit` text `message` was last built from |
| `learning_log` | this run, and an assistant recording what the person said during a rework | what each edit taught, dated |

Plus the operational columns your drafting process already keeps: `status` (drafted, approved, sent), the channel, `draft_ref` and `draft_prepared_at` for a prepared email draft, `proofread_log`, `sent_at`.

**The invariant: `message` is never older than the person's latest wording.** `applied_edit` is what lets the run hold it without undoing its own work. Comparing `your_edit` with `message` directly would read every proofread fix and every recalibration as a stale message, and copy the older edit back over it the next morning.

**The clock.** Your drafting process runs first, proofreads what the person approved and prepares the email drafts or the LinkedIn text; nothing is sent by either run. This process runs **[an hour later]**, so it sees the morning's drafts and can mend them. If your scheduler runs on UTC, a daylight-saving change moves both local times at once: move the two schedules together, or this run lands before the drafts exist.

## 1. Reconcile, then find the edited rows
- <tool:data_rows> on **[your outreach table]** with `fields` limited to the columns above, `limit=100`, following `next_cursor` until it comes back null.
- **Reconcile first, over every row at every status except sent.** Where `your_edit` carries text that differs from `applied_edit` (trailing whitespace stripped), <tool:data_write> with the row's `id` sets `message` and `applied_edit` to `your_edit`, verbatim, in one call, before anything else in this run. Pass `expected_revision` from the read: the person may be typing in the table while the run works, and a `revision_conflict` means read the row again and recompute, never overwrite.
- This catches the row edited somewhere other than the table, the row whose previous run failed halfway, and the row approved between two runs. **It reaches approved rows, which nothing else in this process rewrites**, because a stale `message` on an approved row is the one that goes out wrong. It does not reach sent rows: that message has left, and the column is a record. Every reconciled row is named in the report with its status: an approved row whose text changed has to be known before anyone presses send.
- **Then, for learning**, a row is in when `your_edit` carries text that differs from `drafted_version` after stripping trailing whitespace. An empty `your_edit` is not a rewrite; it means the draft was fine.
- **Never learn the same edit twice.** Each `learning_log` entry records the edit it came from (the date and the edit's opening words). A row whose current `your_edit` is already recorded has been through; a changed edit is a new one. The reconciliation is not covered by this skip: an edit learned days ago can still have left a stale `message` today, and that is repaired without being learned from again.
- **Verify every write by reading the row back** (<tool:data_rows> with its `id`). A receipt is not evidence the value landed.

## 2. Diff each edit against the draft
- **Name each difference in the person's words, not as a category.** "Cut *for the opportunity to* from the ask" is a difference; "tightened the ask" is not, because nothing can be checked against it later. Count the words removed and added.
- **Paragraph structure counts.** Merging a one-line opener into the paragraph below shows up in a word-level diff as a single line break, and it is often the whole lesson.
- **Subtract the proofread's own corrections.** Read `proofread_log` and remove what the drafting process fixed from the diff. Learning a style rule from a typo fix is how a playbook fills with nonsense.
- **A rework done in conversation carries instructions, not only words.** Read the row's `learning_log` for what the person said before reading the diff: what they said about the change is stronger evidence than the change itself.

## 3. Sort each difference
- **A style trait** generalizes: a padding phrase cut, a researched detail dropped, a register changed, two paragraphs merged, an abstraction replaced with a plain verb.
- **A fact correction** is a name, a spelling, a date, a job title, a company, a number. It never becomes a rule. Log it on the row and stop there for that difference; the row itself still goes through steps 7 and 8.
- **When it is both, split it.** A date removed because it was wrong is a fact correction; a date removed because an exact date reads like a dossier is a trait. When the diff alone cannot tell them apart, treat it as a fact correction and say so. Under-promoting costs one cycle; over-promoting rewrites every other message in step 6.
- Every fact correction goes in the report: a wrong fact usually means the sourcing was wrong on other rows too.

## 4. Count the sighting
Style traits only. The sightings log is a page in your project: one line per candidate trait, the trait in one sentence, the rows it was seen on and the date of each.
- <tool:oto_doc> `op="get"` on **[your outreach playbook]** and **[your general writing rules]** first. An edit that confirms a rule already written adds a dated confirmation to that rule, not a new line in the log.
- **First sighting**: <tool:oto_doc> `op="patch"`, `mode="append"`, with `section` set to the log's heading, one dated line. A patch needs a heading to target and never re-sends the whole page, so two appends cannot overwrite each other. No rule, no recalibration; it is reported as an observation.
- **Second sighting of the same trait, on a different row**: it becomes a rule. Go to step 5, then mark the log line promoted, with the date and where the rule was written.
- **Two sightings in one message are one sighting.**
- **A trait the person stated in words skips the counter.** It is instructed rather than counted, goes straight to step 5, and its log line says so.
- Pass `expected_rev` from the `op="get"` on every patch, so an edit the person makes to the page at the same time is a conflict rather than a lost line.

## 5. Write the rule where it belongs
- **[Your outreach playbook]** for a rule specific to outbound messages: <tool:oto_doc> `op="patch"`, `mode="append"` on its rules section, a dated rule quoting the drafted sentence and the edited one that produced it.
- **[Your general writing rules]** for a rule that reaches past outreach into everything else the person writes. The outreach playbook defers to it.
- **The person's standing note**, so the rule also holds when they write with an assistant outside this table: <tool:oto_guide> `op="read"`, then `op="write"` with `scope="user"` and `delivery="init"`, the body read plus one line. A write replaces the whole body, so never write without reading first.
- **Never rewrite an existing rule to make room for a new one.** Write the new rule and mark the old one corrected, with the date.
- **Mend what cites it.** A new general rule can contradict the playbook, or any other page that cites the old wording. Correct those in the same run, dated. Never edit the reference messages you keep as evidence (the ones that got real replies): they are what the rules are checked against.

## 6. Recalibrate the drafted messages
Only when step 5 wrote a rule. This is what makes the process a system rather than a diary, and it is where it can do the most damage.
- **Every row at drafted, edited or not.** Leaving the edited rows frozen would mean the person's corrections improve every message except the ones they cared about most.
- **On an edited row, the rule goes on top of their text, not instead of it.** Take their wording as the base, change only what the rule concerns, and leave every other choice they made exactly as it stands. If the rule cannot be applied without undoing something they wrote, leave the row and report it as a conflict for them to settle.
- **Three hard limits.**
  1. **Drafted rows only.** Not sent. **Not approved either**: the person signed that wording off, and a rule does not silently rewrite it. Name every approved row the rule would have changed, and let them decide.
  2. **`your_edit` and `applied_edit` are never touched here.** The recalibrated text goes to `message` and `drafted_version`, identical, in one <tool:data_write> call with `expected_revision`.
  3. **Only what the rule reaches.** A rule about the ask changes the ask. It does not license rewriting the opening, re-researching a fact or a general polish. A row that contains nothing the rule is about is left alone, and the report says so.
- Append to each recalibrated row's `learning_log` the rule that caused the change and the date.

## 7. Rebuild the drafts a change made stale
A message changed after its draft was built leaves that draft stale, whether step 1 or step 6 changed it, and the drafting process ran an hour before this one. **A stale draft is the worst outcome of this whole design**, because it looks finished and is not.
- For every row this run changed in step 1 or step 6 that carries a `draft_ref` prepared before the change: <tool:gmail_compose> with the new `message` as the body, the same recipient, subject and **[standing attachments]** your drafting process uses, and no `mode`, so it is saved as a draft. Read `kind` in the response: `draft` is the only acceptable answer, and anything else is reported at once. Replace `draft_ref` and `draft_prepared_at` on the row.
- **Remove the superseded draft**, so two versions never sit side by side: <tool:gmail_message> `op="drafts"` to find it, `op="trash"` on its message id, then list the drafts again. If the old one is still there, name it in the report for the person to delete.
- A new draft can take a while to appear in a Gmail tab that is already open. Tell the person to reload or search the drafts folder rather than conclude it is missing.
- **A LinkedIn row has no draft to rebuild.** Clear `draft_ref` and put the new text in the report, so the old one is not pasted from memory.
- **If a draft cannot be rebuilt**, clear `draft_ref` so the drafting process rebuilds it tomorrow, and say which rows were rebuilt and which were left for tomorrow.

## 8. Stamp what was sent, then report
**Stamp what the person has sent.** This run never sends; it only records that they did. For each approved row with a `draft_ref`:
- **Email**: <tool:gmail_message> `op="search"`, `query="in:sent to:<recipient> after:<date of draft_prepared_at>"`. A sent message to that recipient with the draft's subject sets `status` to sent and `sent_at` to its date. A draft that has disappeared from the drafts list is not proof of a send, since it may have been deleted; only the sent message counts.
- **LinkedIn**: <tool:linkedin_unipile_chat> `op="list"` (25 threads per page, paged by `cursor`) to find the thread by `attendee_profile_url`, then `op="read"`. A message sent after `draft_prepared_at` stamps the row. Decide who wrote a message by comparing its sender id with your own seat's identity from <tool:linkedin_unipile_profile> `op="me"`; the thread listing's "is sender" flag has been seen false on threads the account wrote last.

**Append to `learning_log` on every row touched**: the date, what changed, and whether it was logged, promoted, recorded as a fact correction, or recalibrated by a rule from another row.

## Feeding it
Two rules for anything else that writes to the table. They are not in conflict: the first is for text an agent wrote, the second for text the person settled.
- **Any agent drafting into the table** writes `message` and `drafted_version` with identical text in one call, and leaves `your_edit` and `applied_edit` empty.
- **Any assistant reworking a message with the person outside the table** writes the agreed text into `message`, `your_edit` and `applied_edit`, identical, in one call, before that conversation ends, adds to `learning_log` what the person said about the change, and leaves `drafted_version` alone. It is the one case where anything but the person writes `your_edit`: without their text in their box there is no diff, and the lesson is lost. Writing only `message` loses the lesson; writing only `your_edit` leaves a stale message in front of them until the next run.

## Output
One report to the person, in this order:
1. What they edited, and what was learned from it.
2. Traits logged once and waiting on a second sighting.
3. Every fact correction, since a wrong fact usually means wrong sourcing on other rows too.
4. **Every other message recalibrated and every draft rebuilt**, since those are changes they did not make themselves, plus any conflict left for them to settle.
5. **Every approved row a rule would have changed but did not touch.**
6. **Every row reconciled in step 1**, with its status and where the newer wording came from; any superseded draft that could not be removed; and the rows stamped as sent.