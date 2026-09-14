# Close stale and duplicate issues in Linear and Zoho Desk

**When to use it**: the tracker and the help desk have filled up with issues nobody will pick up, the same request filed twice, and support tickets that were answered but never closed. Search and triage get slower every week. This cleans both in small, capped batches of reversible actions: a dry run first, nothing applied until the PM says ok, and every change logged with the value it replaced.

```
              Natural language input in Claude
              "Clean up the backlog: tag what's stale, close duplicates and support tickets left open."
                         │
                         ▼
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  1 · Read the tracker            │  │  2 · Read the help desk          │   zohodesk_tickets
│  Open Linear issues with state,  │  │  Tickets still open or on hold,  │   zohodesk_ticket_threads
│  labels, priority and last edit. │  │  and who wrote the last reply.   │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Classify by rule                           │   linear_issue
│  Stale, duplicate, resolved but left open:      │   linear_label
│  three rules of rising risk, each capped.       │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing to do   no item matched a rule
                         ▼  candidates found
┌─────────────────────────────────────────────────┐
│  4 · Post the dry run                           │   slack_post_message
│  Every proposed action with its before-state,   │   data_write
│  logged and posted, nothing applied.            │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Review the list                            ║
║  The PM approves all of it, one rule, or none;  ║
║  the ok covers this exact list only.            ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ not approved    no ok given, nothing applied
                         ▼  approved items only
┌─────────────────────────────────────────────────┐
│  6 · Apply and log                              │   linear_issue
│  Re-read each item, skip any that moved, apply, │   zohodesk_update_ticket
│  and log the prior value so it can be undone.   │   data_write
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and no item is touched
```

## 1. Read the tracker
- <tool:linear_team> `op: "list"` for the teams in scope, then `op: "states"` for each: it maps every state name to its type (backlog, unstarted, started, completed, canceled). The rules only ever change issues in backlog and unstarted states. Note the team's canceled-type state here too, or its "Duplicate" state if the workflow has one, for rule B.
- <tool:linear_issue> `op: "list"` per team with `updated_before` set to today minus **[your stale threshold, e.g. 90 days]**, paginated with `first` and `after`. The list sorts by creation date, so a stale issue can sit on any page: bound the read with the date filter instead of walking every issue and filtering locally. Each issue comes back with its state type, priority, assignee, labels and `updatedAt`.
- <tool:linear_issue> `op: "list"` a second time with `created_after` set to the last cleanup run (or **[e.g. 30 days]**): the recently filed issues are the ones most likely to repeat an older one, and they are the only candidates for rule B.
- <tool:linear_label> `op: "list"` to find the id of your `stale` label. If it doesn't exist, the dry run lists "create label `stale`" as its first proposed action; it isn't created before approval.

## 2. Read the help desk
- <tool:zohodesk_tickets> once for `status: "Open"` and once for `status: "On Hold"` (one status per call), `sort_by: "customerResponseTime"` so the longest-silent customers come first, paginated with `from_index` and `limit`. Escalated tickets are never read, so no rule can reach them.
- <tool:zohodesk_ticket_threads> for each ticket older than **[your resolved threshold, e.g. 14 days]** since the customer last wrote. The ticket's modified time moves whenever an agent edits a field, so it proves nothing about the conversation. The thread list does: the most recent thread must have `direction: "out"`, meaning your team wrote last and the customer hasn't answered since.

## 3. Classify by rule
Three rules, in rising order of risk. Each has its own cap per run; when a rule matches more than its cap, take the oldest first and report how many are left for the next run. Never raise a cap mid-run.

- **A — stale** (tracker): backlog or unstarted, no assignee, low or no priority, no update since the threshold, not already labeled `stale` → add the `stale` label. Default cap **[20]**. Linear's priority scale is 0 = no priority, 1 = urgent, 2 = high, 3 = medium, 4 = low: match 0 and 4 explicitly. A filter like "priority ≥ 3" silently misses unprioritized issues, and sorting by the number puts "no priority" above urgent. Adding the label bumps `updatedAt`, so the label, not the date, is what marks an issue as already handled on the next run.
- **B — duplicate** (tracker): for each recently filed issue, <tool:linear_issue> `op: "search"` with its key terms in two or three phrasings (search matches title and description words, and two people rarely title the same request alike). A pair counts only when both sit in the same team and describe the same symptom or request. The older issue is canonical and may be in any open state; the newer one gets closed, and only if nobody has started or been assigned to it. If the older match is already completed, it isn't a duplicate but a possible regression: flag it, don't close it. Default cap **[15]**.
- **C — resolved but left open** (help desk): the last thread is outgoing, and the customer has been silent past the threshold → close it. Default cap **[15]**. Two checks before turning this rule on: your help desk must reopen a closed ticket when the customer replies (that setting is what makes a close reversible from their side), and if it emails the customer on close, with a notice or a satisfaction survey, this rule is customer-facing: keep its cap low.

## 4. Post the dry run
- <tool:data_write> one row per proposed action into **[your cleanup log table]** (create it once), in a single batch call with `key` pointing at a field that joins system, item id, rule and run date, so re-running the same dry run updates its rows instead of duplicating them. Each row carries the item's link, the rule and why it matched, the proposed action, the **before-state** (the state id, the full list of label ids, the ticket status), the `updatedAt` or modified time seen now, and the status `proposed`. The before-state is what makes every action reversible: an undo replays it.
- <tool:slack_post_message> the report to **[your product ops channel]**: grouped by rule, one line per item with its link, the reason it matched and the action, then the number left over by each cap. <tool:slack_list_channels> resolves the channel name to the ID the call needs. A long report is split into threaded parts, not truncated: if the response carries `split_into`, don't post it again. A message can't be edited, so a correction means deleting and posting again.

## 5. Review the list
The PM answers in the conversation that started the run: "ok", "ok for A and C", or "ok except" followed by the items to leave alone. The approval covers exactly the items in the report. A reaction in the channel from someone else isn't an approval, and no answer means nothing is applied.

## 6. Apply and log
- **Re-read before every write.** <tool:linear_issue> `op: "get"` (it takes the human-readable identifier) or <tool:zohodesk_ticket>, and compare the update time with the one logged in step 4. If it moved, someone touched the item after the dry run: skip it and log `skipped: changed since dry run`. For rule C, check <tool:zohodesk_ticket_threads> again, since a customer reply since the dry run disqualifies the ticket.
- **A:** if the `stale` label was approved for creation, <tool:linear_label> `op: "create"` it once, before anything else. Then <tool:linear_issue> `op: "update"` each issue with `label_ids` set to the issue's current label ids plus the `stale` id. `label_ids` replaces the whole set: sending only the new id strips every other label off the issue.
- **B:** first <tool:linear_comment> `op: "create"` on the canonical issue with anything only the duplicate carries (the account that asked, a repro step, a quote), so nothing ends up living only on a closed issue. Then comment "Duplicate of" plus the canonical's identifier on the duplicate, and <tool:linear_issue> `op: "update"` its `state_id` to the duplicate or canceled state from step 1.
- **C:** <tool:zohodesk_update_ticket> with `data: {"status": "Closed"}`.
- Never `op: "delete"` or `op: "archive"` on an issue, and never trash a ticket. A label and a state change keep the item searchable and take one call to reverse.
- <tool:data_write> each row to `applied` or `skipped`, with the time. To undo a run, select its applied rows and write back the logged before-state (the full label list, the prior state id, the prior status), through the same preview and ok.

## Output
For each rule: proposed, approved, applied, skipped because the item changed, and left over by the cap. Plus the link to the dry-run report, the log table, and anything held for a person (a label that needed creating, rule C left off because the help desk doesn't reopen on reply).