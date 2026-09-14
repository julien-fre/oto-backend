# Write a problem statement from customer requests

**When to use it**: a cluster of requests has come out of triage and someone is about to write a spec for it. First turn the cluster into one problem statement the team can say yes or no to, built from what customers wrote, what they said on calls and what the product already does. Skip this step and the spec drifts toward the loudest request instead of the problem underneath it.

```
              Natural language input in Claude
              "Frame the problem behind the report export requests before anyone writes a spec."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull the requests in the cluster           │   productlane_threads
│  Every thread on the topic, counted by          │   productlane_tags
│  distinct account rather than by message.       │   productlane_companies
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Check what already exists                  │   linear_issue
│  Issues open and done, live projects, and       │   linear_project
│  any framing page already written.              │   notion_search
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Hear it in the customers' words            │   granola_content
│  Calls matched by attendee domain, quoted       │
│  verbatim with account and date.                │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Draft the problem statement                │
│  Problem, who suffers, impact, root cause       │
│  and up to three solution paths.                │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Can it be said in one sentence?            ║
║  Who can't do what, and why, backed by two      ║
║  accounts and two kinds of evidence.            ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ not ready       sent back to triage
                         ▼  one sentence, corroborated
╔═════════════════════════════════════════════════╗
║  6 · Review the draft                           ║
║  A person reads it before it becomes the page   ║
║  the spec will cite.                            ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Publish and link                           │   notion_create_page
│  Create or revise the framing page, then link   │   notion_append_blocks
│  it from each related issue, once.              │   linear_comment
└─────────────────────────────────────────────────┘

▪ terminal — the cluster goes back to triage and nothing is published
```

## 1. Pull the requests in the cluster
- <tool:productlane_tags> — threads have no free-text search, so resolve the cluster to an id first. When triage tagged it, `op=list` with `name_contains` on the cluster's name gives the `tag_id`.
- <tool:productlane_companies> — otherwise, `op=search` with `name_contains` or `domain` gives a `company_id` for each account triage named.
- <tool:productlane_threads> — `op=search` on that `tag_id`, or once per `company_id`, bounded with `created_after` to **[your lookback window]**. Search `status=open`, `status=snoozed` and `status=done` all three: a snoozed thread is a pain someone parked, and a thread closed with a workaround is still evidence that the pain exists. Walk `cursor` to the last page at `limit=200` rather than stopping at the first one.
- When the cluster has neither a tag nor named accounts, search the whole lookback window with no filter and classify each thread by reading it. Slower, but a cluster known only by its name has no other path to its threads.
- For each thread, `op=get` with `expand=['messages']` and read the customer's own messages. The thread's comments are the team's internal notes: useful context, but not what the customer said, and paraphrases there drift toward the team's vocabulary.
- **Count distinct accounts, not threads.** One account raising the same request by email, chat and the portal is one voice heard three times. Group by `company_id` before counting anything: twelve threads from two accounts is a different problem from twelve accounts.
- Keep `pain_level` as read. `HIGH` gets counted on its own, and `UNKNOWN` is not `LOW`, it means nobody set it.
- <tool:productlane_companies> — `op=get` on each distinct company, for whatever **[your segment fields]** you keep on the company, which is what "who suffers" will be written from, and for its domains, which step 3 matches calls on.
- Read only. `op=send` on a thread messages the customer through the channel they wrote from, and nothing in this process calls it.

## 2. Check what already exists
A badly framed problem is often a feature you already have, hidden or broken. Check before writing a word of the statement.
- <tool:linear_issue> — `op=search` (full text over title and description) with two or three phrasings. The customer's plain words and the technical name the team uses rarely match on the first try. Read every hit's state: an issue marked done that customers still describe as missing is the most important finding a run can make, because it means the feature shipped and nobody can find it, or it doesn't work for them.
- <tool:linear_project> — `op=list` scoped to the team that owns the area, to see whether a live project already covers the cluster. A framing that duplicates a running project splits one discussion in two.
- <tool:notion_search> — look for a framing page already written on this topic, `sort=last_edited_time`. If one exists, this run revises it rather than publishing a second page with a different conclusion.
- Classify the capability as exactly one of four, because each points to a different kind of solution: **exists**, **missing**, **hidden** (it exists and customers can't find it) or **broken** (it exists and doesn't work for them).

## 3. Hear it in the customers' words
- <tool:granola_content> — there is no full-text search over notes, so bound the read instead: `op=list_notes` with `created_after` set to the same lookback window, plus `folder_id` when your team files customer calls in a folder (`op=list_folders` to find it). `page_size` tops out at 30, so walk `cursor` to the end.
- The list returns only each note's id, title, owner and dates: no attendees, no summary. So `op=get_note` on every listed note, then keep the notes whose attendee email domains match the domains of the step 1 companies, after dropping your own domain and any note-taker bot. **A title that names an account is a shortcut, never the filter.** A customer call titled "Weekly check-in" names nobody, and a note dropped at list time is dropped without a warning, taking the quotes step 5 needs with it. The bounded window is what keeps a `get_note` per note affordable.
- Pull the transcript only when the summary mentions the topic without quotable words. The note is already in hand, so go to `op=get_transcript` and walk `cursor` from the start: a second `op=get_note` with `include="transcript"` saves nothing, and returns `TRANSCRIPT_TOO_LARGE` on a long meeting.
- Keep quotes verbatim, each with its account and call date. **Separate what the customer said from what your team answered.** "We're working on it" said on a call is not evidence the problem is solved, and "that already exists" is a lead for step 2, not proof.

## 4. Draft the problem statement
- **Problem, in one sentence**: *[who] can't [do what] because [why].*
- **Who suffers**: segment and role, with scale as the distinct accounts from step 1, never the thread count.
- **Business impact**, in your own units: **[revenue at risk]**, **[deals blocked]**, **[support load]**. Only figures you can trace to a thread, a deal or a ticket. An impact you can't source goes in as an open question, not as a number.
- **What exists**: the classification from step 2, with the issue identifiers.
- **Root-cause hypothesis**, with its evidence: quotes (account and date) and issue identifiers.
- **Up to three solution paths**: a name, one line, effort (S/M/L) and confidence (H/M/L). High confidence needs two independent kinds of evidence agreeing, a thread and a call, not two threads from the same account.

## 5. Can it be said in one sentence?
The bar: the problem sentence names a who, a what and a why, and at least two accounts and two kinds of evidence back it. When it can't be written, because the threads describe three different pains or all of the evidence comes from one account, the cluster isn't ready. Send it back to triage with what you found and publish nothing. A framing forced out of thin evidence becomes a spec nobody can defend.

## 6. Review the draft
A person reads the draft before it becomes the team's reference. This is the page the spec will cite, and the review is where "who suffers" and the impact figures get challenged. Nothing is written anywhere until they say it can be.

## 7. Publish and link
- <tool:notion_create_page> — under **[your product decisions parent page]**, titled with the cluster's short name, the statement as blocks. Notion takes at most 100 blocks in one request, so create the page with the first 100 and send the rest with <tool:notion_append_blocks> in batches of 100. When step 2 found an existing framing page, append a revision section to that page instead of creating a twin.
- <tool:linear_comment> — one comment on each related issue from step 2, linking the page, so the next person who searches the tracker for this topic lands on the framing instead of starting a new one. The process is re-run to revise a framing, so before each `op=create`, `op=list` the issue's comments and skip it when one already carries the page link: the page URL is the dedup key, and without the check every revision posts a second link on every issue.
- Never change an issue's state or priority here: deciding is the team's job, and the page is what they decide from.

## Output
The page link and the problem sentence; how many distinct accounts, threads and calls the statement rests on; the capability classification; the solution paths with effort and confidence; and the issues that received a link. When step 5 stopped the run: which part of the sentence couldn't be written and what evidence was missing.