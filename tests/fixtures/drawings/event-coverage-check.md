# Check new features have PostHog tracking before release

**When to use it**: features reach production without the events to measure them, and by the time someone builds the dashboard the data doesn't exist. This runs while the ticket can still change: every weekday it reads the pull requests of tickets in a pre-production state, checks whether anything already measures the new behavior, and when nothing does, tells the assignee on the ticket what to emit. It proposes event names; it never writes code, never writes to PostHog and never changes a ticket.

```
              Scheduled routine, every weekday morning
              "Which tickets heading to production have no analytics events?"
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Resolve the pre-production states          │   linear_team
│  Read the team's states fresh and keep the ids  │
│  of the ones that mean about to ship.           │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no states         none of your named states resolve
                         ▼  the states resolve
┌─────────────────────────────────────────────────┐
│  2 · Take the tickets not yet checked           │   linear_issue
│  Every issue now in those states, minus each    │   data_rows
│  ticket already checked once.                   │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing new       a quiet day, and it says so
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Is it even measurable?                     ║   the ticket text and its labels
║  A refactor, a fix or an internal change ships  ║
║  no user-facing behavior to count.              ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ not measurable    no new user-facing behavior
                         ▼  a user-facing behavior ships
┌─────────────────────────────────────────────────┐
│  4 · Read the code that is shipping             │   github_search
│  The files its pull requests change, read for   │   github_pulls
│  analytics capture calls.                       │   github_files
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no pull request   no row, seen again next run
                         ├───────────────▶  ▪ truncated         at the file cap, recorded unknown
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Does anything already measure it?          ║   github_search
║  The branch's own capture calls, then main,     ║   posthog_schema
║  then the events PostHog has seen.              ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ already covered   an event exists for it
                         ▼  nothing measures it
┌─────────────────────────────────────────────────┐
│  6 · Name what is missing                       │   proposed, never imposed
│  The behavior, an event name in your            │
│  convention, two or three properties.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Tell the person who can still fix it       │   linear_comment
│  One comment on the ticket for its assignee,    │   slack_post_message
│  one line in the channel for the whole run.     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Record the verdict                         │   data_write
│  One row per ticket checked, so the same ticket │
│  is never raised twice.                         │
└─────────────────────────────────────────────────┘

▪ terminal — the ticket stops there; its row is written and counted, except at no pull request, which writes none
```

The steps follow one ticket through. Steps 3 to 7 run once per ticket not yet checked, and on most days most tickets leave at `not measurable` or `already covered`, which is the point: a check that flags everything gets muted within a week. Step 7 writes one comment per ticket but **one** channel message for the whole run.

## Before the first run
- **The pre-production state names** for your team (**[e.g. In Review, Ready for QA, Staging]**), stored in a config table. A person names them once; the run resolves them to ids every time.
- **Your PostHog project id** (**[your PostHog project id]**), passed as `project_id` to `posthog_schema`, so step 5 reads the project your product actually sends to.
- **The repository** the features ship from (**[owner/repo]**) and **the analytics call your code uses** (**[e.g. `posthog.capture("event_name", {...})` or your own `track()` wrapper]**), so step 4 knows what a capture call looks like.
- **An event coverage table** keyed on `issue_key`: `title`, `state`, `assignee`, `pr_numbers`, `verdict` (`gap`, `already_covered`, `not_measurable`, `unknown_truncated`), `events_found`, `event_proposed`, `properties_proposed`, `commented`, `run_date`, `run_id`.
- **Your engineering channel** in Slack, with the app a member of it.

## 1. Resolve the pre-production states
- <tool:linear_team> `op="list"` for the team, then `op="states"` with its `team_id`. Resolve each configured state name to its `state_id` **on every run, never hardcoded**: a renamed state silently matches nothing, and the run would report a quiet day forever.
- **Stop at `no states` if none resolves.** A run that guesses which states mean "about to ship" checks the wrong tickets, and a wrong nag costs more trust than a missing one.

## 2. Take the tickets not yet checked
- <tool:linear_issue> `op="list"` once per resolved `state_id`, paging with `after` until the last page. **No `updated_after` bound, on purpose.** A ticket that left at `no pull request` can sit unchanged in its state for days, and when its pull request finally opens without being linked on the ticket (found only through GitHub search in step 4), nothing on Linear moves its `updatedAt`: a date-bounded read would drop it, and it would never be checked. A pre-production state holds a small set of tickets, so walking it whole is cheap, and the coverage table does the dedup.
- <tool:data_rows> on the event coverage table, `filter={"issue_key": {"in": [<the keys>]}}`. **Drop every ticket that already has a row**, whatever state it has moved through since. One nag per ticket, ever, and this filter is the whole reason the check stays welcome. It is also the run's only memory: a ticket that left at `no pull request` has no row, so it comes back every weekday while it stays in the state.
- When no ticket survives, the run leaves at `nothing new` and still posts its counts line.
- `op="get"` on each survivor for its title, description, labels and assignee.

## 3. Is it even measurable?
A gap only exists where there is behavior to count. The ticket leaves at `not measurable`, with a row saying why, when it is:
- a refactor, a dependency bump, a build or infrastructure change, or tests
- a bug fix that restores behavior that was already instrumented
- an internal or admin-only tool with no customer surface
- a copy or visual change with no new action a user can take

What survives lets a user **do something they could not do before**, or changes how they do it. Saying so out loud is what stops the run commenting on a version bump.

## 4. Read the code that is shipping
- Find the ticket's pull requests: the ones your Git integration linked on the ticket, else <tool:github_search> `op="issues"`, `q="repo:<owner>/<repo> is:pr <ISSUE-KEY>"`, which matches the key in a pull request's title or body.
- **A ticket with no pull request leaves at `no pull request` and gets no row**, on purpose: the code isn't written yet, so nothing can be missing from it, and tomorrow's run sees the ticket again.
- <tool:github_pulls> `op="files"` with the pull request `number`, `per_page=100`, paging with `page`. In the added lines of each patch, look for your analytics call and record every event name the pull request adds.
- **`op="files"` is capped at 3,000 files and omits large patches without an error.** For a changed file that came back with no patch, <tool:github_files> `op="read"` on its `path` twice: once with `ref` set to the pull request's head branch and once with its base branch (both from `github_pulls` `op="get"`). A whole-file read shows every capture call in the file, including ones that were there long before this pull request, so **credit the pull request only with the calls present at head and absent at base**; an old call counted as new would mark a real gap as covered. A file the pull request adds has no base version, and every call in it counts.
- When the file count is at the cap, the list is incomplete and looks clean: record `unknown_truncated` and leave at `truncated`, never as a gap.

## 5. Does anything already measure it?
Three checks, in this order, and the first that answers yes ends it. The event isn't named until step 6, so each check looks for a **candidate stem**: the object noun of the behavior from step 3, the module or feature name an event about it would start with in your convention.
- **The branch itself.** A capture call added in step 4 whose event name carries the stem means the ticket is covered.
- **Main.** <tool:github_search> `op="code"`, `q="repo:<owner>/<repo> <your capture call> <the stem>"`, so the hits are files holding capture calls rather than every mention of the module; a hit only counts once the call itself carries the stem. This catches a feature that extends one instrumented earlier. **Code search indexes the default branch only**, skips files over 384 KB, needs at least one real term (`repo:` alone returns nothing), and returns match locations, not file content (read a hit with `github_files` `op="read"`). It has its own low rate limit, around thirty requests a minute, so search once per stem, never once per file.
- **PostHog.** <tool:posthog_schema> `op="events"` with `project_id`, **called once per run with no `search`**, before the first ticket; each ticket's stem is then matched against that list locally, so a busy day costs one call, not one per ticket. **It lists the event types the project has seen**, so an event that exists in code but has never fired is absent, and a pre-production feature has by definition never fired. A gap is therefore never declared from PostHog alone; and an empty list for the whole project means the project has no data yet, not that nothing is instrumented.

Any yes is `already covered`, recorded with what was found, and no comment is written. Silence from all three, with a pull request that adds no capture call, is the gap.

## 6. Name what is missing
Three things per gap, and no more:
- **The behavior**, in one sentence, in the words your product uses for the module.
- **The proposed event name**, built on the stem from step 5 and following the convention visible in the event list read there (**[e.g. `object_verb` in snake case, past tense]**). Where existing names are inconsistent, say so and follow the largest family rather than inventing a new style. The name is proposed, never imposed: naming belongs to the team, and a check that dictates it gets muted.
- **The two or three properties** without which the event answers nothing, usually the account, the plan and whatever distinguishes the paths through the feature. Not a schema, not a wish list.

Tie it to the question someone will ask: adoption, a funnel step, or whether the thing gets used twice. A gap with no question behind it isn't worth an engineer's afternoon and isn't raised.

## 7. Tell the person who can still fix it
- <tool:linear_comment> `op="create"` with `issue_id`, one comment per ticket, addressed to the assignee, at most six lines:

```text
No analytics on this one yet
<the behavior, one sentence>
Suggested event: `<name>` · properties: <a>, <b>, <c>
Nothing in this branch captures it, and PostHog has never seen it.
Worth adding before this merges; after it ships, the first weeks of data are gone.
```

- <tool:slack_post_message> **once for the whole run** to your engineering channel: one line per gap (issue key, title, assignee, proposed event), then `Checked: <n> · already covered: <n> · not measurable: <n> · unknown: <n>`. Five separate messages about five tickets is how a useful check becomes noise. A run with no gaps posts the counts line alone, because a silent run and a broken run must not look alike.
- To tag the assignee, <tool:linear_user> `op="get"` for their email, then <tool:slack_find_user_by_email>. A Linear name is not a Slack id: an assignee who won't resolve is named in plain text and reported as unmapped, never silently untagged.
- The app must be a member of the channel or the post fails with `not_in_channel`.

## 8. Record the verdict
- <tool:data_write> with `rows=[...]` and `key="issue_key"` on the event coverage table: one row per ticket that reached a verdict (`gap`, `already_covered`, `not_measurable`, `unknown_truncated`), with the events found, the event and properties proposed, and whether a comment was written. A ticket that left at `no pull request` gets no row.

## What this never does
- Never writes code, opens a pull request or pushes a commit. `github_actions` `op="dispatch"` and `github_pulls` `op="merge"` sit in the same connector as the reads above, and either would fire a real build or write to a protected branch.
- Never writes to PostHog: no flags, no insights, no annotations.
- Never moves a ticket's state, creates an issue or adds a label. The only writes are one comment per ticket, one Slack message and the coverage rows.

## Output
Per run: one Linear comment on each ticket that ships a user-facing behavior with nothing measuring it, carrying the proposed event and its properties; one Slack message listing each gap with its owner, plus the counts checked, already covered, not measurable and unknown; and one row per ticket in the coverage table, so no ticket is ever raised twice.