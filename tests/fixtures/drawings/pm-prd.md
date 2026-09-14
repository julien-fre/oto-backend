# Write a PRD in Notion backed by call quotes and usage data

**When to use it**: a problem is framed, the user stories are filed, and the team now needs one document that lets engineering start, design mock and sales announce. Readable in ten minutes, two to four pages. A good PRD isn't complete, it's decisive: every claim traces to a customer's words, a measured number or an issue in the tracker.

```
              Natural language input in Claude
              "Write the PRD for bulk CSV export. The framing and the stories are done."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Check the prerequisites                    │   notion_search
│  Find the framing page and the filed stories    │   linear_project
│  before a single section is written.            │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not ready       no framing or no stories yet
                         ▼  framing and stories found
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  2 · Pull the customer's words   │  │  3 · Measure the baseline        │   posthog_insight
│  Exact quotes from recent        │  │  The number the success metric   │   posthog_query
│  Granola call transcripts.       │  │  moves, from a saved chart.      │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 └───────┬─────────────────────────────┘
                         ▼  evidence in hand, or its absence noted
┌─────────────────────────────────────────────────┐
│  4 · Check scope against the tracker            │   linear_issue
│  What is already built, in flight or owned      │
│  by another project gets named, not moved.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Draft the seven sections, then cut         │
│  One line up top, a success metric at the end,  │
│  cut down to two to four pages.                 │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · Settle the open questions                  ║
║  The PM decides or assigns each one before      ║
║  the draft becomes a page.                      ║
╚════════════════════════╤════════════════════════╝
                         ▼  no open question without an owner
┌─────────────────────────────────────────────────┐
│  7 · Write the PRD page                         │   notion_create_page
│  A draft page under your specs parent,          │   notion_append_blocks
│  linked from the project it specifies.          │   linear_project
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and no PRD is drafted
```

## 1. Check the prerequisites
- <tool:notion_search> — find the framing page (the problem statement) and any story pages by the feature's name, `sort: "last_edited_time"`. The search only sees pages shared with the integration, so an empty result means "not shared, or not written": ask which before concluding. Read the framing in full with <tool:notion_get_blocks> `recursive: true` — toggles and nested lists hide half a framing at the first level. The tool reads one page of 100 blocks per level and doesn't follow `has_more`: if the response has `has_more: true`, or a nested block comes back with exactly 100 children, the framing is longer than it can read, so say so and don't write the PRD from a partial framing.
- <tool:linear_project> — find the project the stories were filed under (`op: "list"`, then `op: "get"`), and list its issues with <tool:linear_issue> `op: "list"`, `project_id`. The stories are those issues; their acceptance criteria are what section 5 links to.
- **If the framing or the stories are missing, stop and say which.** A PRD written without a framing invents its problem; one without stories has an empty middle. Both are their own processes, don't improvise them here.
- Note the three to five product areas the framing says are affected. The PRD cites them; it doesn't rediscover them.

## 2. Pull the customer's words
- <tool:granola_content> `op: "list_notes"` with `created_after` set to **[your evidence window, e.g. the last 90 days]**, narrowed with `folder_id` if your customer calls live in a folder (`op: "list_folders"` to find it). There is no text search on notes, and the list carries little more than ids, titles and dates: shortlist by title and date, then `op: "get_note"` on each shortlisted call to read its summary and attendees before keeping it. Drop calls that don't touch the feature and calls whose attendees are all internal.
- A note's summary is a paraphrase, not a quote. For the two or three lines that go into "Why now", pull the exact wording with `op: "get_transcript"` (paginated, `page_size` up to 100). `op: "get_note"` with `include: "transcript"` fails with `TRANSCRIPT_TOO_LARGE` on long meetings, so go straight to the paginated call for long meetings.
- Keep each quote with the account and the call date. If no call in the window mentions the problem, write that down: a PRD with no customer voice should say so rather than borrow one from the framing.

## 3. Measure the baseline
- <tool:posthog_insight> `op: "list"` with `search` on the feature's terms, then `op: "run"` on the chart the team already watches. It replays the insight's own definition, so the baseline matches the number people see on their dashboard. Prefer it to any query you would write yourself.
- <tool:posthog_query> only when no saved chart exists, or when `run` reports the chart is saved in PostHog's legacy `filters` format and can't be replayed. Confirm the event is tracked first with <tool:posthog_schema> `op: "events"`. Use `query` with a `FunnelsQuery` or `RetentionQuery` for conversion and retention (funnel semantics don't survive a SQL rewrite), and HogQL only for plain counts, counting people with `uniq(person_id)`, not `distinct_id`, which counts devices. For a legacy chart, rebuild its exact definition (same events, steps and window), not a new one of your own.
- **No event, no baseline.** Write "not measured today" and make instrumenting the event part of the success criterion. Never estimate a baseline to fill the section.
- Record the window and the exact definition next to the number, so the post-launch reading uses the same one.

## 4. Check scope against the tracker
- <tool:linear_issue> `op: "search"` on each in-scope item, two or three phrasings each, every phrasing one to three key terms ("csv export", "bulk download"), never a sentence: the search matches the query as a substring of titles and descriptions, so a full sentence finds nothing. A customer's wording and an engineer's issue title often describe the same thing in different words.
  - Found and **done** → it already exists: move it to "out of scope" or reframe the item as fixing or surfacing what's there.
  - Found and **in progress** in another project → a dependency, named in section 6 with its project.
  - Found **in someone else's backlog** → a merge candidate, flagged for the PM.
- <tool:linear_project> `op: "list"` — an active project that overlaps the scope is a dependency or a conflict, and the PRD says which.
- This step only reads. The PRD names what it found; it never re-parents, merges or closes an issue.

## 5. Draft the seven sections, then cut
1. **In one line**, readable by someone outside the product team.
2. **Why now**: the quotes from step 2, the baseline from step 3, the accounts or revenue affected as **[your impact measure]**, and the prioritization context from the framing.
3. **Audience**: the target persona, who is explicitly not targeted, and the accounts that asked.
4. **Scope**: what's in, and what's explicitly out, including what step 4 found already built.
5. **User stories and acceptance criteria**: linked to the issues, not pasted in.
6. **Dependencies and risks**: from step 4, each with its owner.
7. **Success criterion**: the metric from step 3, its baseline, the target **[your target]**, and when it will be read.

Then cut to two to four pages: enough for dev to start, design to mock, sales to announce, no more. Link rather than copy. Every "TBD" becomes either a decision or an open question listed at the top.

## 6. Settle the open questions
Show the draft with its open questions first, then what step 7 will write: the parent, the page title, and the link line to be added to the project in the tracker. The PM decides each question or assigns it to a named owner; nothing unowned survives into the page. A draft in the conversation costs nothing to rewrite, a page people have already read and quoted does.

## 7. Write the PRD page
- <tool:notion_search> the exact title first. If a PRD for this feature already exists, stop and ask whether this replaces it; never create a second copy.
- <tool:notion_create_page> under **[your specs parent page or database]**, title "PRD: **[feature name]**". Under a database, the call writes the title into a column named `Name`, and the database schema call doesn't return columns, so read a few rows with <tool:notion_query_database> `page_size: 5` first: the title column is the property whose `type` is `title`. If it isn't named `Name`, or the database is empty so no row shows it, the create would be refused, so the page goes under a parent page instead. Set a status property to Draft where one exists; sharing with the wider team is a separate step.
- Notion takes at most 100 blocks per request and 2,000 characters per text run. Pass the first 100 blocks in `content`, then <tool:notion_append_blocks> the rest in batches of 100, in order, splitting long paragraphs. Keep the page id from the create call: if a batch fails, resume appending to that page rather than creating it again.
- <tool:linear_project> `op: "update"` to link the PRD from the project's description. The update replaces the description whole, so `op: "get"` first and append the link line to the existing text. That field is Linear's short project summary, capped at 255 characters, not the project document: if the appended text would pass the cap, leave the description as it is and report the link instead. A failed update never blocks the run; the PRD page already exists.

## Output
The PRD page link, whether the project in the tracker now links to it (or the link to add by hand), the open questions with their owners, which evidence was missing (no call in the window, no tracked event), and the scope overlaps found in the tracker.