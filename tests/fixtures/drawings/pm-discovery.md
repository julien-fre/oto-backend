# Recruit customers for interviews and write the guide

**When to use it**: a problem has been framed and the team is about to commit to a solution on the strength of it. Before that, set up a short round of interviews with customers and a few colleagues who talk to them, designed to prove the hypothesis wrong rather than to collect polite agreement. A tepid yes from people picked because they already complained is not evidence.

```
              Natural language input in Claude
              "Set up interviews to test whether account admins really rebuild their reports by hand."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Write the hypothesis to invalidate         │   notion_search
│  Read the framing, then write down in advance   │   notion_get_blocks
│  what would prove it wrong.                     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Choose who to interview                    │   hubspot_property
│  People who raised the pain, plus a few in      │   hubspot_object
│  the same segment who never did.                │   hubspot_owners
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Read what they already said                │   granola_content
│  Past calls, so each interview starts from a    │
│  real moment instead of re-asking.              │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write the interview guide                  │   notion_create_page
│  Ten open questions at most, and a prep         │   notion_append_blocks
│  note per person from their own history.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Propose slots, draft invitations           │   calendar_event
│  Three free slots each, no slot offered         │   gmail_compose
│  twice, every invitation saved as a draft.      │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · Review, then send by hand                  ║   gmail_message
║  A person reads each draft and clears it with   ║
║  the account owner.                             ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ held back         the account owner said not now
                         ▼  sent from the mailbox
┌─────────────────────────────────────────────────┐
│  7 · Stamp the sent invitations                 │   gmail_message
│  Stamp each sent invitation once per round,     │   hubspot_property
│  with a note on the contact record.             │   hubspot_object
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  8 · Confirm and book each accepted slot        ║   gmail_message
║  Rerun as replies arrive; a person confirms     ║   calendar_event
║  each slot before it is booked.                 ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ answered by hand  ambiguous reply or slot taken
                         ▼  confirmed and booked
┌─────────────────────────────────────────────────┐
│  9 · Run the interviews                         │   human
│  Note the workaround, budget, real frequency    │
│  and any pain raised unprompted.                │
└─────────────────────────────────────────────────┘

▪ terminal — the process takes no further step for that person
```

## 1. Write the hypothesis to invalidate
- <tool:notion_search> — find the framing page for this problem, then <tool:notion_get_blocks> with `recursive=true`. Without it, anything nested inside a toggle or an indented list comes back as a parent block with its children missing, and evidence quotes are commonly tucked away exactly like that.
- Write the hypothesis in this form: *We think [segment] suffers from [pain] because [cause]; if that is true, we'd see [observable behavior].*
- Then write the kill criterion before any interview happens: *We'll consider it wrong if [signal], for example most interviewees have no workaround and can't recall the last time it happened.* Written in advance, it stops a lukewarm round from being read as confirmation afterwards.
- No framing page? Work from the request itself, but write both sentences anyway. Without them there is nothing for the interviews to test.

## 2. Choose who to interview
- <tool:hubspot_property> — `op=list` on `companies` and on `contacts` once, before any filter. HubSpot's internal names are not the labels in the interface, and a filter written from a label is a guess.
- The `contacts` list also shows whether **[your research-invite date property]**, the stamp step 7 writes, exists yet. When it doesn't, nobody has been stamped: nobody is excluded on it this round, and nothing is created here. This step only reads, and the property is created in step 7, after the review.
- <tool:hubspot_object> — `op=search` on `companies`, with `filters` on **[your segment properties]**. Filters combine with AND only, and pages stop at 100: walk `after`. Build two groups:
  - **The accounts that raised the pain**, named in the framing's evidence. Find each one with `op=search` and a `query` on its name or domain, since the framing names them and doesn't carry CRM ids.
  - **One or two accounts from that segment search that never raised it.** If they turn out to have the pain too, the problem is wider than the requests showed. If they don't, and they work the same way, the hypothesis has a hole. People chosen only because they complained can do neither.
- For each company, `op=get` with `associations=["contacts","deals"]`: the associated ids come back inline, one call instead of an `op=associations` round trip per object type. Ask for the company's owner, `domain` and `country` among the returned `properties` in the same call. The associations are ids only, so the stages take one more `op=get` per associated deal with `properties=["dealstage","pipeline"]`.
- Choose one contact per company with `op=get` on each associated contact, asking for email, job title, time zone (`hs_timezone` in a standard portal, confirm it in the property list) and the research stamp when it exists: the role that lives with the workflow, not the most senior name on file. Skip a contact with no email rather than guessing one, and skip anyone opted out of email.
- **Exclude an account with an open deal in [your late pipeline stages].** A research call in the middle of a negotiation reads as a sales tactic. Take those stage ids from your pipeline settings, because `hubspot_property` returns an empty option list for `dealstage` (stages belong to a pipeline, not to the property).
- **Exclude anyone invited to research in the last [your cooldown].** Read the stamp that step 7 writes, **[your research-invite date property]**, back as a returned property and drop recent ones from the result. Expressing "never stamped, or stamped before the cutoff" as a filter would take two searches, since filters are AND-only.
- **Stop at [your round size] external interviewees**, the accounts that never raised the pain counted inside that number, not on top of it. Apply the exclusions before cutting: cut first and exclude after, and the round comes up short.
- <tool:hubspot_owners> — one call, then resolve each company's owner id to a name and email. The owner hears about the interview before their customer does (step 6), and the owners of these accounts, along with a support lead, are the natural internal interviewees.

## 3. Read what they already said
- <tool:granola_content> — there is no full-text search over notes, so bound the read: `op=list_notes` with `created_after` set to **[your lookback window]**, and `folder_id` when customer calls are filed in a folder. `page_size` tops out at 30, so walk `cursor` to the end, then `op=get_note` on every note in that window. The list carries only each note's id, title, owner and dates, with no attendees and no summary, so a match made at list time rests on the title alone and misses fail silently, leaving an interviewee with an empty prep note.
- Match each note's attendee emails to the chosen contact's email first, then to the company's `domain` from step 2, after dropping your own domain and any note-taker bot. A call with a colleague of the interviewee still says how their account works.
- Only then read the summary, and pull the transcript only when it touches the topic. The note is already in hand, so go to `op=get_transcript` and walk `cursor` from the start: a second `op=get_note` with `include="transcript"` saves nothing, and returns `TRANSCRIPT_TOO_LARGE` on a long meeting.
- For each person, keep two things: **the last concrete moment they described** around this workflow, which becomes the anchor for "tell me about the last time…", and **what they already told you**, so the interview doesn't spend ten minutes re-asking it.
- If someone's own words already contradict the hypothesis, flag it now. That is a finding, not a reason to drop them from the list.

## 4. Write the interview guide
- <tool:notion_create_page> — under **[your research parent page]**. Notion takes at most 100 blocks in one request: create the page with the first 100 and send the rest with <tool:notion_append_blocks> in batches of 100. The page holds:
  - **The hypothesis and the kill criterion**, word for word from step 1.
  - **The guide, ten questions at most**: context (2), then pain as open questions (3 to 4: "tell me about the last time you…"), then the current workaround (1 to 2, the strongest indicator that the pain is real), then impact ("if this disappeared tomorrow, what would change?"). If you can, ask them to share their screen and show you.
  - **Wording rules**: never a closed question about the pain; never "would you pay for…", since people are unreliable about their own future; always "tell me" or "show me".
  - **A prep note per interviewee**: role, account, the anchor moment and what not to re-ask, from step 3.
  - **A signals table, one row per interview**, filled in afterwards: a workaround they already built, a budget mentioned, real frequency, pain raised unprompted, and whether the kill criterion was met.

## 5. Propose slots, draft invitations
- <tool:calendar_event> — `op=list` on the interviewer's calendar from now to **[your scheduling horizon]**, and on the note-taker's too if their calendar is shared with you (it appears in `calendar_calendars`, which lists only the calendars the connected account can read). Raise `max_results`: at the default of 20, two busy weeks are silently cut off and the "free" slots after the cutoff are not free.
- Find 30-minute windows, with a buffer on each side, inside **[your interviewing hours]**, and state each one in the invitee's time zone, read from the contact in step 2 and falling back to the company's country when the contact has none.
- **Three slots per person, and never the same slot offered to two people.** If two people accept the same slot, one has to be moved, and that reads worse than having fewer options.
- <tool:gmail_compose> — one invitation per person, leaving `mode` at its default so each one is saved as a draft and nothing leaves the mailbox. Keep it short: why them (their role, and the moment from step 3 if they raised it with you), that this is research and not a sales call, 30 minutes, no promise about the product, and the three slots. CC the account owner.

## 6. Review, then send by hand
- <tool:gmail_message> — `op=drafts` lists what was created, for the person reviewing to read against the guide. A fresh draft can be missing from a Gmail tab that was already open, so reload the tab or search `in:drafts` before concluding it wasn't saved.
- Before anything is sent, the reviewer clears each invitation with the account owner, who can hold any of them back with no reason needed. That person gets no invitation this round.
- If step 2 found no **[your research-invite date property]**, say so here: step 7 will create it, and the reviewer should know the CRM schema is about to change before it does.
- **The reviewer sends the drafts from their mailbox.** The process does not compose the same message again with `mode="send"`: that leaves the draft behind, and a draft sent later emails the customer twice. The one message the process itself ever sends to a customer is the calendar invite in step 8, and only for a slot the reviewer has confirmed.

## 7. Stamp the sent invitations
- **The round start** is the day step 5 created the drafts. The run reports it, and every rerun of steps 7 and 8 is given it: it stays fixed however many passes there are, which is what makes both steps safe to rerun.
- <tool:hubspot_property> — before the first write, `op=get` on `contacts` with `property_name` set to **[your research-invite date property]**. When it doesn't exist yet, `op=create` it with a `definition` of `type="date"` and `fieldType="date"` (plus name, label and group), so the first update isn't refused for writing to a property that isn't there.
- <tool:gmail_message> — `op=search` with `in:sent to:[invitee email] after:[round start]` plus the invitation's subject, to confirm each invitation actually left. A draft still sitting unsent is not an invitation, and stamping it would take that person out of the next round for nothing. The subject keeps an unrelated email to the same person from passing for the invitation.
- <tool:hubspot_object> — **the stamp is the dedup key.** Read the contact's stamp first (`op=get` with it among `properties`). Empty or earlier than the round start: `op=update` it to the day the invitation was sent (the `date` of the sent message, not the day of the run), then `op=add_note` with the hypothesis being tested, so the account owner sees it on the record. On or after the round start: this contact was stamped on an earlier pass, so skip both. Without that check, every rerun would push the stamp later, quietly extending that person's cooldown, and leave another copy of the note on the record.

## 8. Confirm and book each accepted slot
- <tool:gmail_message> — rerun this step as replies come in: `op=search` with `from:[invitee email] after:[round start]`, and read which slot each person picked.
- <tool:calendar_event> — **first, check the reply isn't already booked**: `op=list` on the interviewer's calendar with `query` set to the invitee's email, from the round start to **[your scheduling horizon]**. The list carries no guests, so `op=get` each hit and look for the invitee in `attendees`. If they're there, the reply was booked on an earlier pass: skip it and report it as already booked. Without this check, the free-slot check below finds that event occupying the slot and sends a finished booking back to the reviewer as a conflict.
- Then check the picked slot is still free with `op=list` over that window: slots were proposed days ago and calendars move.
- **Show the reviewer every parsed acceptance before booking anything**: the person, the slot, and the line of the reply it was read from. "Thursday doesn't work, maybe the week after" is not a yes to Thursday, and a misread reply booked with invitations on becomes a real invite in the customer's calendar. Anything ambiguous, and any slot taken since it was offered, goes back to the reviewer to answer by hand.
- <tool:calendar_event> — after the reviewer's OK, `op=create` with the invitee and the note-taker as `attendees` and `send_updates="all"`. The default, `"none"`, adds the guests without emailing anyone, and the customer never receives the invite. **Pass `end` explicitly as `start` plus 30 minutes**: left out, it defaults to an hour and blocks twice the interview in the customer's calendar. Write both as ISO 8601 with the offset of the invitee's time zone, so the event lands at the time the reply accepted. Say in the description that notes will be taken, and ask for consent at the start of the call.

## 9. Run the interviews
A person runs each interview from the guide, with a second person taking notes. After each one, fill in its row in the signals table. After the round, read the table against the kill criterion before anyone reads it for confirmation: a workaround already built, a budget mentioned and pain raised unprompted are signals, while "yes, that would be nice" is not one.

## Output
The guide's page link and the round start; the interview list split into the two groups, plus the internal interviewees; the slots proposed per person; the drafts created; who was held back and by whom; and, on later runs, who was stamped and who was skipped as already stamped this round, which acceptances were confirmed or sent back to the reviewer, and which interviews were booked on this pass or found already booked.