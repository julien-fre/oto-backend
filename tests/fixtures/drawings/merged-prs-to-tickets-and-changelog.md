# Update Linear tickets and draft the changelog from merged PRs

**When to use it**: merged code and the board drift apart within a day. A pull request lands, its ticket sits in the wrong column, nobody tells the team what shipped, and whoever writes the release notes rebuilds the list from memory at the end of the month. This closes all three loops after each deploy, or nightly: every merged pull request moves its ticket forward with a comment saying why, the customer-visible changes become one unpublished changelog draft, and the team gets one short digest.

```
              Scheduled routine, nightly, or after a production deploy
              "What shipped today? Move the tickets, draft the changelog, post the digest."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Check the tools and read the config        ║   linear_issue, productlane_changelogs
║  A live call to each tool, and each repo's      ║   data_rows
║  deploy rule from your config table.            ║   a live call, never a status field
╚════════════════════════╤════════════════════════╝
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Find what each repo deployed               ║   github_actions
║  Every deploy since the last run: completed     ║   never dispatch, never rerun
║  is not the same as succeeded.                  ║
╚════════════════════════╤════════════════════════╝
                         ▼  each repo's window, deployed or not
┌─────────────────────────────────────────────────┐
│  2 · List the pull requests in the window       │   github_search
│  Merged inside the window, cut off on each      │   github_pulls
│  merge time, one pass per deploy status.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already processed  on a row, deploy status unchanged
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Match each pull request to its ticket      │   linear_issue
│  An explicit key in the title, branch or body;  │
│  a bare #123 is GitHub's own number.            │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Comment, then move forward                 ║──▶  Linear  one comment, at most one forward move
║  Cite the pull request, then the state its      ║   linear_comment
║  deploy earns; never backwards.                 ║   linear_team
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Draft the changelog entry                  │──▶  Productlane  published false, never broadcast
│  Customer-visible changes that reached          │   productlane_changelogs
│  production, in your customers' words.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Post the digest                            │──▶  Slack  600 characters, three names per repo
│  One message under the cap, or one line         │   slack_post_message
│  saying nothing merged.                         │   data_write
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · A human publishes the changelog            │   the one human step
│  The draft is edited and published by a         │
│  person, never by the run.                      │
└─────────────────────────────────────────────────┘

▪ terminal — handled by an earlier run, nothing is written for it again
```

## 0. Check the tools and read the config
- **A connector's status field is not evidence it works; a live call is.** A connector can read as active and fail, or read as not selected and answer. Make one cheap read on each: <tool:linear_issue> with `op="list"` and `first=1`, <tool:productlane_changelogs> with `op="search"` and `limit=1`, and <tool:github_repos> with `op="get"` on each repo. If Linear doesn't answer, still run steps 1, 2, 5 and 6, and have the digest say out loud that tickets were not updated. If Productlane doesn't answer, skip step 5 and say that too.
- <tool:data_rows> on your config table, one row per repo: owner and name, its **[deploy rule]** (a merge deploys production; a merge deploys staging only; a tag deploys production; or a library that ships only when a dependent repo bumps its pinned version), the deploy workflow's file name, for a repo whose tag deploys production the release workflow's file name as well, and for a library the dependent repo and the path of its dependency manifest. Plus: the Linear team key, the names of **[your review state]** and **[your done state]**, **[your customer-visible rule]** (a label or a title convention), the Slack channel id, and whether keyword matching is allowed.
- Read the time from the clock at run time, never assume it. The clock start is now minus 26 hours: slightly more than a day, so schedule drift never drops a pull request.
- <tool:linear_team> with `op="states"` for each team, resolved fresh every run. **States come back unordered, and `position` only compares within a type**: a completed state can carry a lower position than an in-review one. Order by type first (`triage`, `backlog`, `unstarted`, `started`, `completed`), then by `position` inside a type. `canceled` and `duplicate` states sit outside that order and are never touched.

## 1. Find what each repo deployed
- <tool:github_actions> with `op="runs"`, `workflow` set to the deploy workflow's **file name**, the default branch, and `per_page=30`, paging back until a run started before the window's lower bound. The file name is more stable than the numeric id, but a renamed workflow returns an empty list, which looks exactly like a quiet day: an empty list on a repo that has a deploy rule is reported, never skipped.
- **Read `status` and `conclusion` together.** `completed` includes runs that failed, and `conclusion` stays null while a run is still going. Only a `conclusion` of `success` means the code reached where that workflow deploys.
- **After a deploy**, the window is that deploy: from the start of the previous successful run of the same workflow to the start of the run that triggered this one, so the pull requests in between are what it shipped.
- **Nightly**, the window is every deploy since the last check, not only the newest: from the start of the newest successful deploy an earlier run recorded on the ledger (with none recorded, the clock start) up to now. On a repo where every merge deploys, several merges mean several deploys; a window drawn only around the newest one would silently drop every pull request shipped by the ones before it, and no later window would ever include them.
- **Each pull request gets its own deploy status**, whatever the window: deployed when a successful run of the deploy workflow started after its `merged_at`, otherwise "merged, not deployed". For a repo whose tag deploys production, the same test runs on the release workflow: in production only when a successful release run started after `merged_at`, staging only when just the deploy workflow passed that test. A library has no deploy of its own and uses the clock start.
- A failed or unfinished deploy doesn't stop the run. Those pull requests are merged but not live: that caps their tickets at the review state and keeps them out of the changelog. They stay above the next nightly lower bound, so the run after a successful deploy picks them up again (step 2).
- **`op="dispatch"` and `op="rerun"` are never called.** Dispatch triggers a real run, which on a deploy workflow is a deployment; it is a dry run by default, so an accidental `dry_run=false` is the entire failure. Rerun replays the whole run and can redeploy.

## 2. List the pull requests in the window
- <tool:github_search> with `op="issues"` and `q="repo:<owner>/<repo> is:pr is:merged merged:>=<date of the window start>"`, `per_page=100`, paging until a page comes back short; one query per repo stays well under the search API's low per-minute limit. **A date-only `merged:>=` returns a deliberately wider slice**; the exact cut comes next, and trusting the date query alone lets in pull requests merged hours before the window.
- <tool:github_pulls> with `op="get"` on each candidate the ledger doesn't already settle (below): `merged_at` for the exact cut against the window and for step 1's deploy test, plus the head branch, the body and the labels. A merged pull request is `closed`, because GitHub has no merged state; a closed one with a null `merged_at` was abandoned.
- **Don't read the diff.** `op="files"` stops at 3,000 files and `op="commits"` at 250, and a large pull request comes back truncated with no error. The title, branch, body and labels are complete, and they are all this needs. On a busy repo, list first and fetch bodies only for the pull requests inside the window: a listing that carries every body can outgrow a single response.
- <tool:data_rows> on the ledger, keyed on repo plus pull request number. A key already there whose recorded deploy status still holds takes the **already processed** exit. A row recorded as "merged, not deployed" or staging only, that a successful deploy or release now covers, goes through again: the deploy is the new fact, and step 4 moves the ticket for it without re-announcing the pull request. Consecutive windows overlap on purpose, because schedules drift; the ledger, not the query, is what stops a second comment on the same ticket.

## 3. Match each pull request to its ticket
- **An explicit key first**: `<TEAM KEY>-<number>` in the title, then the branch name (a branch like `eng-42-fix-export` counts, case-insensitive), then the body. A bare `#123` or `repo#123` is a GitHub reference, not a ticket key.
- <tool:linear_issue> with `op="get"` and the key as `issue_id`: the human-readable identifier resolves directly and returns the ticket's state with its type, its team and its description. `op="search"` does not resolve identifiers, and a search on the literal key comes back empty. Confirm the returned identifier equals the key before acting. If `op="get"` ever refuses a key, search on words from the pull request's title instead and accept only a result whose identifier equals the key.
- **The keyword fallback, only when your config allows it**: two or three targeted `op="search"` queries per pull request theme, never a load of the whole board. An empty result is a valid "no match". A non-empty one is not a match either: broad words return a page of loosely related tickets, so read each candidate's description. A near miss's own text can name the right ticket ("tracked separately in …"): read it before discarding it. **A keyword match can move a ticket to the review state at most, never to done.**
- Several pull requests on one ticket form one cluster and get one comment. When a specific ticket and its parent epic both fit, update only the specific one: two tickets for one pull request is noise, not thoroughness.
- Skim the whole body, not only the title: a pull request's headline can hide a second change that matches a different ticket.
- No match: the pull request skips step 4 and still reaches steps 5 and 6.

## 4. Comment, then move forward
The target state follows the repo's deploy rule, checked per repo, never assumed across them:
- **A merge deploys production, and the pull request is deployed by step 1's test**: the done state, unless the ticket's own checklist still holds items no merge performs (a credential to create, a flag to switch on, an ops step). Then the review state: a deploy covers only what the pull request changed.
- **A merge deploys staging only, and a tag deploys production**: the review state at most, unless the pull request is in production by step 1's test: <tool:github_actions> with `op="runs"` on the release workflow's file name shows a `success` run that started after its `merged_at`.
- **A library**: nothing ships until the dependent repo pins a version that includes it. <tool:github_files> with `op="read"` on the dependent's manifest, on its default branch, and compare. Not pinned yet: the review state at most, or no move if you can't tell. The pin bump often lands in its own pull request, sometimes before this window opened, so read the manifest rather than looking for the bump.
- **No successful deploy has started since the merge** (the deploy failed or is still running): the review state at most.

**Never backwards.** Read the ticket's current state and compare it to the target with the ordering from step 0. At or past the target: no move. If this pull request is new progress the ticket has never had cited, still comment, without a state change. If it was already cited (on the ledger, or in <tool:linear_comment> with `op="list"`), do nothing: that is an overlapping window, not news.

Otherwise, **comment before moving**: <tool:linear_comment> with `op="create"`, citing the pull request's number, title and link, the repo's deploy status, and which of the ticket's items it closes and which it leaves open. For a pull request going through again only because its deploy has now succeeded, the comment names that deploy and nothing more. Then <tool:linear_issue> with `op="update"` and the resolved `state_id`. A state that changes with no trace of why looks exactly like a state that is wrong.

<tool:data_write>, the pull request's ledger row (updated in place for one going through again), right after its Linear calls return: repo, number, title, link, merge time, ticket key, action (moved, commented, already ahead, no ticket) and deploy status. A failure after that costs a digest line, never a second comment.

## 5. Draft the changelog entry
- Keep only the pull requests that meet **[your customer-visible rule]** and that are in production by step 1's test for that pull request, not by its repo's newest deploy. With no rule configured, nothing counts as customer-visible: no entry is drafted, and the digest lists the titles for a person.
- Group them by kind (new, improved, fixed) and by product area. One line per group, saying what the reader can now do, in **[your product's customer-facing vocabulary]**: never a branch name, an internal component, a ticket key, a customer's name, or a figure you cannot source.
- If your team records its daily syncs, the sync can supply the one clause of "why" a title never carries: read the meeting's written summary before any transcript, and open a transcript only for what the summary leaves out. It informs the wording; it is never quoted, and if a sync is the only source for a claim, the claim stays out.
- **One draft per run, across all repos.** Every repo's production changes from this run go into the same draft. The run's ledger row (keyed on the triggering deploy run id, or the date for a nightly run) says whether one was already created; a re-run never creates a second. An unpublished draft from an earlier run is someone's work in progress: never edit it, list it in the digest as still awaiting review.
- <tool:productlane_changelogs> with `op="create"` and `fields={"title": "<release name or date>", "content": "<the entry>", "published": false}`. **`published: false` is the whole safety of this step**: an entry created published is live the moment the call returns.
- **`op="broadcast"` is never called, in either mode.** It emails every subscribed contact and posts to the configured Slack channels with no recall, and it is a dry run by default, so an accidental `dry_run=false` is the entire failure. Broadcasting also never touches `published`: a broadcast draft sends readers a link to a page they cannot see.
- <tool:data_write>: the run key, the changelog id, the pull request numbers, status draft. Nothing customer-visible means no Productlane call and one row saying so.

## 6. Post the digest
- <tool:slack_post_message> to the configured channel. **Hard cap of 600 characters, counted before posting**: this is a daily alignment ping, not a changelog.
- One bullet per repo that had merges, skipping the rest: the count, the deploy status (split when some are live and some are not), **at most three named pull requests**, and everything else folded into "+N more". Name a ticket only where one matched, inline, as its key and new state. Don't write "no linked ticket" beside each pull request: most days most have none, and the repeated phrase is what makes a digest read like a bot log.
- One line for the changelog (a draft awaiting review, or nothing customer-visible) and one for any tool that didn't answer in step 0.
- **Nothing merged anywhere**: still post one line saying so. Silence on a quiet day is indistinguishable from a broken routine.
- **Over the cap, cut the weakest named item first, then the weakest bullet.** Never compress by abbreviating every word: three well-chosen names beat eight half-names.
- `not_in_channel` means the app lost its membership: <tool:slack_join_channel> and retry once. `account_inactive` means the workspace token itself is dead: don't retry, don't fall back to another channel, and tell a person another way. Steps 3 to 5 stand on their own either way.
- <tool:data_write>: the message's `ts` on the rows of this run, and per repo the id and start time of the newest successful production deploy this run saw (the release run, for a repo whose tag deploys production). That row is the next nightly run's lower bound; a run that fails before writing it only leaves the next window wider, and the ledger absorbs the overlap.

```text
Shipped · <date>
• <repo>: <n> merged, live in production · <#a title> (<KEY-a> → Done), <#b title>, <#c title> +<n> more
• <repo>: <n> merged, staging only · <#d title> (<KEY-b> → In Review) +<n> more
• Changelog: draft in Productlane, awaiting review
```

## 7. A human publishes the changelog
A person opens the draft in Productlane, rewrites what needs it, publishes it, and decides separately, by hand, whether to broadcast it. The run never publishes, never broadcasts, and never edits a draft a person may already have touched.

## What this never does
- Never dispatches, reruns or cancels a workflow, and never merges or reviews a pull request.
- Never creates, closes or cancels a ticket, and never moves one backwards.
- Never publishes or broadcasts a changelog entry.

## Output
Per run: each repo's window, its lower bound (a recorded deploy or the clock start) and the deploys it covered; each pull request's deploy status; pull requests merged, already processed, matched by key and by keyword; tickets commented, moved (and to what), and already ahead; the changelog draft created, or nothing customer-visible; the digest posted with its character count; and any tool that did not answer.