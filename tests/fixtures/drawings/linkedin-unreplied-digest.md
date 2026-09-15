# Draft replies to unanswered LinkedIn messages

**When to use it**: you prospect or network on LinkedIn from your own profile, and the replies pile up between meetings. A "sounds interesting" waits two days, a question goes unanswered, and nobody notices until the thread has gone cold. Four times a day, this pages your LinkedIn inbox back to where the last pass stopped, keeps the threads where the other person wrote last, drafts a reply in your own style and delivers it to you as a Slack DM. It never sends anything on LinkedIn, and it learns from what you actually send.

```
              Scheduled routine, four times a day
              "Show me the LinkedIn threads waiting on me since this morning, with a draft for each."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Page the inbox back to the watermark       │   data_rows
│  Twenty threads a page, newest first, until a   │   linkedin_unipile_chat
│  page falls behind the last pass.               │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Who wrote last?                            ║   linkedin_unipile_profile
║  The last sender's id against your own; the     ║
║  sender flag on the message cannot be trusted.  ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ you wrote last    nothing is waiting on you
                         ▼  the other person wrote last
┌─────────────────────────────────────────────────┐
│  3 · Read the thread and classify               │   linkedin_unipile_chat
│  Refusal, question, interest, off-topic or      │
│  other, judged on the whole conversation.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Qualify the company on a lukewarm reply    │   fr_search, fr_get
│  Only when a company leader leaves the door     │   linkedin_unipile_profile
│  ajar: the registry, then the company page.     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Draft in your own style                    │
│  Short, first name, ends on a question; two     │
│  variants when the company was qualified.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Deliver as a Slack DM                      │   slack_open_dm
│  Name, profile link, last message and draft;    │   slack_post_message
│  an empty pass still says so in one line.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Log each thread, move the watermark        │   data_write
│  One row per thread with its draft, then the    │
│  mark, even when no thread was kept.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · You send it yourself                       │   the one human step
│  Copy, edit or ignore the draft on LinkedIn;    │
│  the run never sends a message.                 │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  9 · Learn from what you actually sent          │   linkedin_unipile_chat
│  Later passes compare your reply to the draft;  │   data_write
│  style rules change only when three agree.      │
└─────────────────────────────────────────────────┘

▪ terminal — the thread is not drafted or delivered on this pass
```

## Before the first pass
- **Your own id.** <tool:linkedin_unipile_profile> with `op="me"` returns the provider id of the connected account. Store it with the config. Step 2 compares every thread against it and step 9 looks for it; nothing else on a message reliably says who wrote it.
- **The connection lives in one workspace.** Pass the workspace that holds the LinkedIn connection explicitly on every call. A scheduled session that starts elsewhere does not resolve it and fails as if the account were not connected.
- **The log table.** One table, keyed on the thread's chat id, with `contact`, `profile_url`, `last_message_at`, `delivered_at`, `status`, `draft`, `actual_reply` and `learned`. It also holds two kinds of control rows: `__watermark__` and one `__run__<timestamp>` row per pass. A table created with `data_create_datastore` belongs to you personally by default, even when created inside a workspace. Transfer it right away with `oto_resource` (`op="transfer"`, `resource_type="datastore_namespace"`, `new_owner_org`), or the next scheduled session will not find it and will create a second one.
- **The DM channel.** <tool:slack_find_user_by_email>, then <tool:slack_open_dm> with the parameter `user` (not `user_id`), and store the returned channel id. Look up the email you use in Slack: it is often not the one your other accounts sign in with, and the wrong one answers `users_not_found`.
- **Your style rules.** Read a few dozen of your own sent LinkedIn messages (<tool:linkedin_unipile_chat> with `op="read"`, keeping the messages whose `sender_id` is yours) and write down how you actually write: length, how you open, whether you sign off, how you end. Step 5 starts from these, and step 9 corrects them.

## 1. Page the inbox back to the watermark
- <tool:data_rows> for the row `chat_id="__watermark__"`: its `last_message_at` is the newest message the previous pass handled.
- **No watermark means the last 50 threads, and nothing more.** Never read "no watermark" as "the whole history": a first pass that surfaces two years of threads is a digest nobody reads.
- <tool:linkedin_unipile_chat> with `op="list"` and `limit=20`, then again with `cursor` for as long as the last thread on the page has a `last_message_timestamp` newer than the watermark. Threads come newest first, so the first page that falls behind the mark is the last one read. The tool documents pages of up to 25, but values above 20 have been refused with a 400 on real accounts; 20 is also the default when `limit` is left out.
- **A hard cap of 200 threads a pass.** Past it, deliver what you have and say so in the DM: a pass that never finishes delivers nothing.
- A page of 20 threads is too large to read whole. When the result lands in a file, extract only the chat id, `attendee_name`, `attendee_headline`, `attendee_profile_url`, `last_message_timestamp`, `last_message.sender_id` and `last_message.text`. Names are resolved in a batch that can fail: when an `attendee_names` field appears with a status, the enrichment did not run, so fall back on `name` or `last_message.sender` rather than concluding the thread has nobody on the other end.

## 2. Who wrote last?
- A thread is waiting on you when `last_message.sender_id` is **not** your provider id.
- **Never use `last_message.is_sender`.** It has been observed `false` on every thread of an account, including the ones where the account itself wrote last. Trusting it surfaces the entire inbox.
- **`unread_count` is not a criterion either.** It reads 0 on threads where the other person wrote last and you opened the message on your phone without answering, which is exactly the thread this process exists to catch.
- **Exit ▪ you wrote last**: nothing is waiting on you. Following up on silence after your own message is a different process.

## 3. Read the thread and classify
- <tool:linkedin_unipile_chat> with `op="read"` on the thread's chat id. The last message alone is not enough: "yes, interested" means nothing without knowing what was proposed three messages up.
- A thread whose last message is only a voice note (`attachments[].type == "audio"`, no `text`) cannot be transcribed with these tools. Flag it in the DM, and draft a line asking for the gist in writing. Never invent what the note said.

| Class | Looks like | The draft |
|---|---|---|
| **Refusal** | "no thank you", "not interested" | Short and gracious. No nudge, no pitch. |
| **Question** | A real question about your offer, how it works, the price | Answers it, then offers a slot. |
| **Interest** | "sounds good", "send it over", "let's talk", a thumbs-up on a pitch | Your **[calendar link]**, or a light next step. |
| **Off-topic** | An inbound pitch, spam, an automated sequence | Flagged, draft left empty: you decide. |
| **Other** | Neither sale nor refusal: a recruiting thread ending on a pleasantry, a congratulation | A minimal draft if one makes sense, and the DM says it was not forced into a class. |

## 4. Qualify the company on a lukewarm reply
**"Why not", "you never know", "send it anyway" are not interest.** They are a door left ajar out of politeness, and the Interest draft (a calendar link) closes it. For these threads, and for any thread where the person runs an identifiable company, qualify the company before writing a word:
- <tool:fr_search> with `query` set to the company name, to get the SIREN, then <tool:fr_get> with it: headcount band, activity code, directors, open establishments, the latest filed ratios and recent legal events. The latest filing can be a simplified one with no revenue line; walk back through `fr_bilans` to the first year that carries one before writing "no revenue". This is the French company registry; for a company elsewhere, use your country's equivalent.
- <tool:linkedin_unipile_profile> with `op="company"` and the company's slug: current headcount, twelve-month growth, industries, and the words the company uses about itself. Company pages are cached for a few hours per account, and the upstream quota on company lookups is small, so qualify the threads that need it, not the whole inbox.
- **Match the person to the company before using any of it**: their name among the directors, or their headline naming the company. The facts of a namesake company are worse than no facts.
- Two lookups, about a minute. Without them the draft can only be generic.

## 5. Draft in your own style
**The general rules**, from your style notes (refined by step 9):
- Short: one to three sentences, never a paragraph.
- Open on the first name. No "Dear", no "I hope this finds you well". No signature.
- End on a question or a link.
- Write in the thread's language.
- A proof point only when selling, and only **[your citable proof point]**, never a figure made up for the occasion.

**The lukewarm format**, for the threads step 4 qualified:
- **One play, never a list**: the one that touches their P&L, worked out from how the company makes money rather than from your catalog. Everything else dilutes it.
- **Never use their trade's vocabulary unless you are sure of it.** A term used slightly wrong in front of someone who has done the job for fifteen years costs more than the proposal earns. When in doubt, cut the line.
- **Offer a deliverable, not a capability**: "I'll send you [the result] for one of your [sites] by [day], you take a look" beats "AI could automate [task]". End on a question answerable in three words ("which site?"), not on a calendar link.
- **No conditional tense, no bullet points, no "etc."** Together they are the signature of the generic AI vendor these leaders hear from every week.
- **Keep the hook of your first message.** If you opened on something personal (a mutual contact, a dated signal), it has to survive into the reply: that is what separates the thread from an automated sequence.
- **Proofread.** A typo in a DM to a company head shows, and it costs.
- Deliver **two variants**: one that commits to a dated deliverable, one that costs them nothing and lets them choose the ground. You pick. This format runs past the three-sentence rule on purpose: it only applies to threads worth a real message.

## 6. Deliver as a Slack DM
- <tool:slack_post_message> to the stored DM channel, one block per thread:

```text
*<Name>* — <headline>
<profile link>
Received <date>: "<last message>"
Draft: <text ready to copy>
```

- For a lukewarm thread, add one factual line about the company before the draft (headcount, trend, sites, revenue when filed) and label the two variants.
- **Above roughly four thousand characters, Slack splits the message** into several and reports it in `split_into`. Read that field before re-posting: a Slack message can be deleted but not edited, so a re-post is a duplicate.
- **An empty pass still sends one line**: "nothing waiting". Silence cannot be told apart from a broken run.
- If the stored channel stops answering, rebuild it with <tool:slack_find_user_by_email> then <tool:slack_open_dm>. An identity check that reports no Slack identity while the token posts fine is not an outage; don't stop the pass on it.

## 7. Log each thread, move the watermark
- <tool:data_write>, one row per delivered thread, keyed on the chat id: contact, profile URL, when the last message arrived, `delivered_at`, the class in `status`, the exact `draft` text as delivered, `actual_reply` empty and `learned=false`. The draft has to be stored verbatim, or step 9 has nothing to compare against.
- **Then move the watermark** to the newest `last_message_timestamp` seen this pass, **even when no thread was kept**, or the next pass rescans the same window. Move it only after the DM went out: a pass that failed to deliver leaves the mark where it was, and the next pass picks the same threads up.
- Write a `__run__<timestamp>` row with threads scanned, kept and delivered in `status`. A scheduled routine that finishes green has not necessarily done its job; this dated line is what makes the history readable without opening a transcript.
- **The watermark is the only thing preventing duplicates.** Two passes started close together produce two digests, so don't fire a manual pass while a scheduled one is running.

## 8. You send it yourself
- You copy the draft, edit it or ignore it, and answer on LinkedIn. The process never calls `op="send"`; keep it out of the scheduled routine's tool allowlist entirely.
- It writes to no CRM, and it never nudges threads where you wrote last.

## 9. Learn from what you actually sent
The digest stays one message with a block per thread, with no checkbox to tick. The signal that you handled a thread is LinkedIn itself: you wrote in it after it was delivered, or you didn't.
- <tool:data_rows> for the rows where `learned` is not true, skipping the `__watermark__` and `__run__` control rows.
- <tool:linkedin_unipile_chat> with `op="read"` on each: look for a message whose `sender_id` is yours and whose timestamp is **after** the row's `delivered_at`.
- **Nothing found**: you haven't handled it yet. Try again next pass. After **[7 days]** with no reply, set `learned=true` with a status saying so, so a dead thread is not rechecked forever.
- **Found**: write it to `actual_reply` and compare it to `draft`. Identical or nearly (a paste): nothing to learn, set `learned=true`. Different: note what changed in `status` (length, opener, tone, what you kept, cut or added), and set `learned=true`.
- **A rule changes only when three or more deltas point the same way** (you always shorten, you never use a given opener). Then update the rules in step 5, with the date and the threads that established the pattern. One edit on one thread is not a pattern; never rewrite the rules on a single example.
- This pass is idempotent by construction: `learned=true` keeps a closed row from being processed twice.

## Output
Each pass: one Slack DM with a block per waiting thread (name, headline, profile link, last message, draft, and two labeled variants plus a company line for a lukewarm reply), or a single "nothing waiting" line. It also reports voice notes that could not be read, and whether the 200-thread cap was hit. In the log table: one row per delivered thread, the moved watermark, a dated run line, and, as you reply, what you actually sent next to what was drafted.