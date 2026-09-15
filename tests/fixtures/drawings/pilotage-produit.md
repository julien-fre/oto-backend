# Take a feature from customer request to release

**When to use it**: a request keeps coming back and you want to take it from scattered customer threads to a decision, then to shipped, without a heavyweight process. One item at a time: frame the problem, size it against real usage, write one decision page, track it, and close the loop.

```
              Natural language input in Claude
              "Customers keep asking for bulk export. Take it from signal to shipped."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Gather what customers said                 │   productlane_threads
│  Every thread on the topic, with its pain level │   productlane_companies
│  and the company behind it.                     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Measure today's usage                      │   posthog_insight
│  How many people touch the flow now, read from  │   posthog_query
│  the team's own charts first.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Find the work that already exists          │   linear_issue
│  An existing issue or project gets extended,    │   linear_project
│  never reopened as new work.                    │   productlane_roadmap
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write the decision page                    │   notion_search
│  The problem, its evidence, a score, the scope  │   notion_create_page
│  and a definition of done.                      │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Go, or park                                ║   notion_update_page
║  A person reads the page and decides; the       ║
║  reason is written back either way.             ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ parked            the page keeps the evidence
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Put it in the tracker                      │   linear_issue
│  One issue carrying the definition of done,     │   productlane_threads
│  linked to every thread that asked for it.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Check it weekly until it ships             │   linear_issue
│  Stalled work ships smaller or drops back; a    │   notion_append_blocks
│  shipped item is measured against done.         │   posthog_query
└─────────────────────────────────────────────────┘

▪ terminal — nothing reaches the tracker; the page stays for the next time the signal grows
```

## 1. Gather what customers said
- <tool:productlane_threads> `op=search` — bound by `created_after` to **[your lookback window]** and, if your team tags feedback by topic, by `tag_id`. Search has no free-text filter, so topical matching happens on the text: page through with the returned cursor (up to 200 rows a page), then `op=get` with `expand=["messages"]` only on the threads that look on topic — reading every conversation in full is the slow part.
- Keep, per thread: the customer's own words as a quote, `pain_level`, the company, and the date. `UNKNOWN` pain is not `LOW` — nobody rated it, so don't read it as mild.
- <tool:productlane_companies> `op=get` — the company behind each thread, plus any tier or segment you keep there. **Count companies, not threads**: one account opening five threads is one signal, and a tally of threads flatters the loudest customer.

## 2. Measure today's usage
Requests say what people want; usage says how many would feel it. Read the team's numbers before writing your own:
- <tool:posthog_insight> `op=list` with a `search` on the flow's name, then `op=run` on the closest saved insight with your own date window. It replays the chart's own definition, so the number matches what the team sees on its dashboard.
- <tool:posthog_query> only when no saved chart fits. Check the real event names first with <tool:posthog_schema> (`op=events`) — a query on a guessed event name correctly returns zero. Count people with `uniq(person_id)`, never distinct ids (those count devices), aggregate inside the query (an unbounded one is capped and only flags that more rows exist), and send funnels or retention as a `FunnelsQuery` or `RetentionQuery` object rather than hand-written SQL, which gives a plausible number that disagrees with the dashboard.
- Write down the exact insight or query behind every number. Step 7 re-runs the same one to check the definition of done.

## 3. Find the work that already exists
- <tool:linear_issue> `op=search` — full text over titles and descriptions, with two or three phrasings: a customer's plain words and an engineer's issue title often describe the same thing differently.
- <tool:linear_project> `op=list`, and <tool:productlane_roadmap> `op=projects` with `sort="total_score"` — which projects already carry customer feedback, ranked by the weight of the requests attached to them rather than by date.
- **Stalled is not absent.** For a project that looks in progress, <tool:linear_issue> `op=list` with its `project_id` and `updated_after` set to **[your stall threshold]** ago: nothing updated means the work stopped, which is a different decision from starting it. Either way, an existing issue or project is extended in step 6, never duplicated.

## 4. Write the decision page
- <tool:notion_search> on the topic first. A decision page that already exists gets a new dated section, not a sibling.
- <tool:notion_create_page> under **[your product decisions database]** (`parent_type="database"`), with a status property set to *to decide*. The body carries, in this order:
  - **Problem** — for whom, what pain, in the customers' quoted words, with links to the threads. No solution in this section.
  - **Evidence** — companies asking and their pain levels (step 1), usage with the insight or query it came from (step 2), existing work (step 3).
  - **Score** — impact × confidence ÷ effort. Confidence is high only when requests and usage point the same way; one loud account with no usage behind it is low confidence however urgent it sounds.
  - **Scope and out of scope** — the smallest version that answers the problem. Options, settings and edge cases nobody asked for go out of scope by default.
  - **Definition of done** — a check someone can run, tied to the step 2 number (the event you expect to move, and in which direction), not "the feature is live".

## 5. Go, or park
A person reads the page and decides; this process doesn't. <tool:notion_update_page> sets the status property to *go* or *parked*, and the reason goes on the page either way. A parked item stops here and nothing reaches the tracker — the evidence stays on the page, so the next time the same request comes back you start from step 4's page instead of from zero.

## 6. Put it in the tracker
- <tool:linear_issue> `op=create` with the team, the project from step 3 when one exists, a title that names the outcome, and a description holding the definition of done and a link to the decision page.
- **If step 3 found an issue, update it instead** — but `description` is replaced, not appended: `op=get` first, then write back the merged text, or you erase what the engineers already wrote.
- <tool:productlane_threads> `op=link` with the issue id, for every thread from step 1. This is what turns a piece of feedback into a traced request, and it raises the score of the project it belongs to.

## 7. Check it weekly until it ships
- <tool:linear_issue> `op=get` for its state, and `op=list` on its project with `updated_after` a week ago for movement.
- <tool:notion_append_blocks> — one dated line on the decision page each week: state, what moved, what is blocking.
- **Nothing stays in progress past [your stall threshold]**: at that point it is cut to a smaller scope that can ship, or it goes back to *parked* with the reason — never left open.
- Once shipped, re-run the exact insight or <tool:posthog_query> from step 2 after **[your measurement window]** and write the result against the definition of done.
- Close the loop with <tool:productlane_threads> `op=comment` on each linked thread — an internal note, visible to your team only, so whoever owns each account can reply in their own words. **Never `op=send`**: it messages the customer directly through the channel the thread came from.

## Output
The decision page link and its status; companies asking, usage and the score behind it; the issue it lives in and how many threads are linked; and, on weekly runs, the state and whether it has crossed the stall threshold. After shipping: the measured result against the definition of done.