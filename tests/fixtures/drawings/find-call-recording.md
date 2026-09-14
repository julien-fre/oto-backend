# Find a past call recording by name, company or email

**When to use it**: someone asks "can you find the call where they mentioned X?" and whoever gets asked opens the CRM, guesses which note holds the link, gives up, and tries the recording tool under a name the call was never logged under. Most of the time the link is already in the CRM from the last person who looked; the rest of the time the recordings resolve it in one pass, as long as the match runs on the company domain as well as the person. Either way the link is saved back, so the same lookup takes one step next time.

```
              Natural language input in Claude
              "Find the recording of our last call with this prospect, here is their work email."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pin down the person and the company        │
│  A work email gives both, a free mailbox gives  │
│  a person only, never a guessed company.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Find the record in the CRM                 │   folk_record
│  The person by email, the company by name and   │   prefix match, so search twice
│  by domain before concluding it is absent.      │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ ask first         two records could be the one
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Reuse a link already saved                 │   folk_group  custom fields
│  A recording field, then the notes, then logged │   folk_record  notes, interactions
│  interactions on the company and its people.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Search the recordings                      │   granola_content
│  Only if the CRM had no link: the exact email,  │   no search on notes, walk the cursor
│  then the company domain, over the window.      │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no call found     said plainly, never rebuilt
                         ▼  the most recent match wins
┌─────────────────────────────────────────────────┐
│  5 · Return the call, save the link             │   folk_record  a note ending with the link
│  Link, date, participants and summary, and the  │
│  link written back when the CRM lacked it.      │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and nothing is written to the CRM
```

The only input is the question typed in chat. Every call in this process is a read, except the write-back in step 5 when the CRM had no pointer to the call. This process never invents a call, a date or a quote: "no call found" is a complete answer, and a call is never reconstructed from notes and presented as a recording.

## 1. Pin down the person and the company
- **An email on a company domain** gives a person and a company. Keep both: the call may be logged against a colleague rather than the person you were given.
- **An email on a free mailbox** (gmail, outlook, icloud and the like) gives a person only. Never derive a company from a free-mailbox domain, and never run the domain match in step 4 on it: every call with anyone on that mailbox provider would come back.
- **A bare company name** may match two companies. Resolve it to one record in step 2 before going further; two similarly named companies are a question to ask, not a guess to make.
- **A first name alone** is not enough to search on. Ask for the company or an email before spending a call.

## 2. Find the record in the CRM
- <tool:folk_record> `entity="person"`, `op="search"`, `filters={"emails": "<the email>"}` for a person. Read the person's companies from the record, then list the people linked to that company with `filters={"companies": {"in": ["<company id>"]}}`: they are the other people the call could be logged under.
- <tool:folk_record> `entity="company"`, `op="search"` for a company. **The CRM's `like` filter matches from the start of the string, not anywhere in it**, so a search on a full compound name can return zero on a company that exists. Search on the name's first word, and separately on the domain, before concluding the company is not there. Always pass a filter: a search with none fetches every page of the workspace.
- **Two records could be the one**: stop and ask which, naming both with their domain and the people on each. A lookup that picks the wrong one returns a confident answer about the wrong company.
- **No record at all** does not end the run: go straight to step 4 with the email or domain you have, and say in the answer that the CRM holds nothing for them.

## 3. Reuse a link already saved
Check three places, in this order, on the company and on each person from step 2. The first link found is the answer; step 4 is skipped.
- **A field that holds a recording link.** <tool:folk_group> `op="custom_fields"` on each group the record belongs to (`entity_type="company"`, then `"person"`), to learn what the field is actually called in your workspace, instead of assuming there is none. Its value sits in the record's `customFieldValues`, grouped by group id, from <tool:folk_record> `op="get"`.
- **Notes.** <tool:folk_record> `entity="note"`, `filters={"entity_id": "<record id>"}`, once per record. The CRM has no text search over notes and no read of one note by id, so list them per record and scan each note's content for **[your recording tool's share-link domain]**, which every pasted call link contains, then for the contact's name.
- **Logged interactions.** <tool:folk_record> `entity="interaction"`, `entity_id="<record id>"`, `when="past"`. The search returns subject and snippet only, 30 per page, and stops at `max_results` with `truncated: true` when more exist; read a full body with `op="get"` only on the interactions whose subject looks like the call. A missing body is a permission outcome, not an empty interaction. An interaction that carries no link but carries a date narrows step 4 to that day.

When several saved links match, return the most recent and say how many others exist, with their dates.

## 4. Search the recordings
Only when step 3 found no link.
- <tool:granola_content> `op="list_notes"`, `created_after` = **[a year back]**, or bracketed with `created_before` to the single day step 3 produced. `page_size=30`, walking `cursor` to the end. There is no search on notes, so the match runs on the listing: read each note's attendees, with `op="get_note"` on the notes whose listing does not carry them.
- **Match twice.** First the notes whose attendees include the exact email. Then, separately, the notes with any attendee on the company's domain (skipped for a free mailbox). A call is recorded under whoever hosted it, so the person you were asked about often sits on a colleague's call rather than their own, and only the domain pass finds it. **If the email pass returns nothing, run the domain pass before concluding there is no call.**
- **Read notes from every owner the key can reach.** A call a colleague ran is recorded in their notes, not yours; if the key only reaches your own notes, say so in the answer, because "no call found" then means "none in your notes".
- **Two notes can be one call.** When two of your colleagues attend, each gets a note: two ids, two links, two summaries of the same meeting. The same calendar event id, the same attendees and the same slot mean one call, returned once with both links.
- **Date the call from the note's creation time**, not from the calendar event attached to it, which can carry a date days away from the call the note summarizes.
- **A note whose only attendees are your own team** is preparation for a call, not a call with them. It is never returned as their recording.
- <tool:granola_content> `op="get_note"` on the winner for title, date, participants, summary and link. Pull the transcript only if the summary is empty: `include="transcript"`, falling back to `op="get_transcript"` when a long meeting returns `TRANSCRIPT_TOO_LARGE`.

**Several calls match**: the most recent wins, and the others are listed with their dates, never dropped silently. **None match**: the answer is "no call found", with what was searched (the email, the domain, the window) so the person asking can widen it.

## 5. Return the call, save the link
Answer with the call's link, its date, the participants, a one-paragraph summary, and the CRM record it belongs to. For a link found in step 3, the date, participants and summary come from the note or interaction that carried it, and no call to the recordings tool is spent.

When the call came from step 4 and the CRM had no pointer to it, write it back so the next lookup for this account ends at step 3:
- **Before writing, list the record's notes again** (<tool:folk_record> `entity="note"`, `filters={"entity_id": ...}`) and check that no note already carries this exact link. Someone may have pasted it since step 3, and the link is the only key that tells two notes about the same call apart.
- <tool:folk_record> `entity="note"`, `op="create"`, `item={"entity_id": "<company id>", "content": ...}`: the call's date, the participants, one line on what it covered, and the link as the note's last line. On the person instead when there is no company record (a free-mailbox contact).
- If your workspace has a recording-link field (found in step 3), set it too: <tool:folk_record> `op="update"` with `fields={"customFieldValues": {"<group id>": {"<field name>": "<link>"}}}`. Custom fields are keyed by group, and the record must already be in that group or the write is refused. Only that field changes; the patch leaves the others as they are.
- Read the note list back once to confirm the note landed. A write receipt is not proof.

Nothing is written for a call returned from step 3, for "no call found", for a lookup that stopped to ask which record, or when the CRM holds no record for them at all: a lookup never creates a CRM record.

## Output
Which step resolved the call (a saved link in the CRM, or a direct search of the recordings); the call's link, date, participants and summary; the CRM record it belongs to; how many other calls matched, with their dates; whether a link was written back and where; and, for "no call found", the email, domain and window that were searched.