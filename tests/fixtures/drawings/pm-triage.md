# Prioritize the backlog from support tickets and issues

**When to use it**: "sort my tickets", "what should we prioritize?", "cluster the backlog". Support tickets and dev issues pile up in two places, each looks urgent on its own, and nobody can say which two or three problems matter most. This turns both flows into a handful of sourced, scored clusters a product lead can decide on in a couple of minutes.

```
              Natural language input in Claude
              "Triage the backlog and tell me what to prioritize this cycle."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read the support queue                     │   zohodesk_tickets
│  Open, escalated and recently closed tickets,   │   zohodesk_search_tickets
│  with the customer and what they wrote.         │   zohodesk_ticket_threads
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Read the dev backlog                       │   linear_issue
│  Open issues bounded by last update, merged     │   linear_label
│  with the tickets they were filed from.         │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Cluster by pain                            │   data_rows
│  Three or more items on one pain, matched to    │
│  last run's clusters by the items they share.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Score with RICE                            │
│  Reach × impact × confidence ÷ effort; high     │
│  confidence needs both sources to agree.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Post a decidable report                    │   slack_post_message
│  Top three clusters with scores and source      │   data_write
│  links, saved for the next run to compare.      │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · A person decides                           ║
║  The report proposes; no ticket or issue        ║
║  changes until someone acts on it.              ║
╚═════════════════════════════════════════════════╝
```

Zoho Desk and Linear are the two sources here; any help desk and issue tracker that can list and search works the same way.

## 1. Read the support queue
- <tool:zohodesk_tickets> — one call per status (`Open`, `Escalated`, `On Hold`), since the filter takes a single status. Page with `from_index` and `limit`: move `from_index` forward by `limit` until a page comes back short. A full page is never the end.
- <tool:zohodesk_search_tickets> — closed tickets too, bounded to **[your lookback window]** on modified time. A bug worked around and closed last month is still recurrence; reading only open tickets makes a chronic problem look new every time.
- Keep, per ticket: number, subject, customer account, priority, status, created date. **Count accounts, not tickets**: one customer opening five tickets on the same thing is one account, and the report must say so.
- <tool:zohodesk_ticket_threads> — only for tickets whose subject is too vague to place ("issue", "urgent", "not working"). The customer's first message carries the actual symptom. Reading every thread is the expensive call; read on demand.

## 2. Read the dev backlog
- <tool:linear_issue> `op=list` per team, with `order_by="updatedAt"` and `updated_after` set to the same lookback window. By default the list is sorted by creation date, so an old issue someone updated yesterday can sit on any page — bound the read by update time instead of walking every page and filtering. Drop completed and cancelled states.
- <tool:linear_label> `op=list` — map your labels to what they signal: **[your bug label]**, **[your feature-request label]**, and whatever marks business pressure (**[your label for customer-reported or deal-risk issues]**). An issue filed or labeled by sales or leadership carries a commercial signal a support ticket rarely does.
- Read Linear priority the right way round: `1` is urgent, then `2`, `3`, `4` by decreasing urgency, and `0` means no priority set — not the lowest.
- **Merge before counting.** An issue created from a support ticket is one report, not two. If the issue description cites the ticket number (or your help desk links the two), fold them into a single item with both sources attached. Skip this and the same complaint counts twice, and worse, it looks like two independent sources agreeing — which is exactly what step 4 rewards with high confidence.

## 3. Cluster by pain
- A cluster is **three or more items on the same pain**, across both flows. Name it in three to five words that describe the pain, not the fix ("exports time out on large accounts", not "rewrite the export job").
- For each cluster record: volume by source, distinct accounts, three to five key items (source and id), commercial signal (from the labels in step 2), urgency (any urgent-priority issue or escalated ticket), and recurrence.
- **Recurrence comes from the last run, not from memory.** <tool:data_rows> on **[your triage log]** returns the previous clusters with their member item ids. Match on shared items, not on names: if half of a previous cluster's items sit in a new one, it is the same cluster even if you'd name it differently today, and it keeps its id. A cluster with no match gets a new id. A cluster seen again, and bigger, is the strongest recurrence signal this process has.
- Items that fit nowhere stay as outliers. One urgent, isolated item is worth a line in the report; it is not a cluster.

## 4. Score with RICE
`reach × impact × confidence ÷ effort`, with each factor written down so the score can be argued with:
- **Reach** — distinct accounts affected in the window (after the merge in step 2).
- **Impact** — per account: 3 blocks their core use, 2 a daily workaround, 1 an annoyance, 0.5 cosmetic.
- **Confidence** — 100% when support and dev sources confirm it independently, 80% for one source plus recurrence from a previous run, 50% for a single source seen once.
- **Effort** — person-weeks, from the `estimate` on the linked issues when they have one; otherwise a rough guess, labeled as a guess in the report.

A cluster with neither commercial signal nor recurrence drops out of the top three however high its raw volume, and the report says why in one line.

## 5. Post a decidable report
- <tool:slack_post_message> to **[your product channel]** — one message: the top three clusters, each with its name, score and the four factors behind it, distinct accounts, three to five source links (ticket number, issue identifier), and one customer quote. Then the notable outliers, one line each, and the clusters that dropped out with the reason.
- Keep it under roughly 4,000 characters. Above that the text is split into threaded parts — nothing is lost, and the response carries `split_into`. Read it before concluding the post was truncated: a Slack message can be deleted but not edited, so a wrong re-post leaves a duplicate in the channel.
- <tool:data_write> — one batch call with `key` set to the cluster id, so each run merges onto the clusters it matched in step 3 and appends only the new ones. One row per cluster: id, name, member item ids, the four factors, the score, and the run date fetched at run time.

## 6. A person decides
The report proposes; the product lead decides. This process never changes a ticket's status, a Linear priority, a label or an assignee. What gets prioritized is decided by whoever reads the report, in the tools where the work lives.

## Output
The Slack message link, plus a short summary in the conversation: tickets and issues read by source, items merged across the two flows, clusters formed and how many matched a previous run, the top three with scores, and anything scored on a guessed effort.