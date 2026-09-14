# Triage bug reports into the right Linear team

**When to use it**: bug reports land in a shared Slack channel from support, sales and the people using the product, and whoever skims them files each one wherever seems reasonable, with a severity taken from how the reporter sounded. The same bug ends up as three issues on two boards, and a regression slips through because nobody searched the closed issues. Every 15 minutes, this reads the new reports, checks the tracker for what already exists, and files each one on the owning team's board with a severity set from impact.

```
              Scheduled routine, every 15 minutes
              "Triage the bug reports posted since the last run and file them."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull the new reports                       │   slack_read_history
│  Posts and threads since the last run;          │   slack_read_thread
│  follow-ups and unfinished filings go to 6.     │   data_rows, data_write
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already handled   nothing pending, no new replies
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Label what the report leaves out           │   data_write
│  Symptom, repro, environment, frequency and     │
│  impact; anything absent marked so.             │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not a bug         a question or a feature request
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Title it on the symptom                    │
│  What was observed; the reporter's guessed      │
│  cause stays in the body, attributed.           │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Catch duplicates and regressions           ║   linear_issue
║  One short term per search, hits read by state  ║   linear_comment
║  type; an open match wins.                      ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Set severity from impact, not tone         │   data_rows
│  A band from what was described, raised by more │
│  accounts or by a regression.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · File it on the owning team's board         │──▶  Linear  a new issue, or a comment on the match
│  Planned on the ledger first, then a new issue  │   linear_issue, linear_comment, linear_team
│  or a comment on the match.                     │   linear_label, data_rows, data_write
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Close the loop in Slack                    │──▶  Slack thread  the issue link, on each filed report
│  A thread reply with the issue link; the alert  │──▶  Slack alert  top band only
│  channel hears only the top band.               │   slack_post_message, slack_join_channel
│                                                 │   slack_read_thread, slack_read_history
└─────────────────────────────────────────────────┘

▪ terminal — nothing is filed for that report; it is still logged
```

Slack is the intake and Linear the tracker here; any channel and tracker that can be read and searched works the same way.

## 1. Pull the new reports
- <tool:data_rows> on your triage ledger, a table whose business key is the report key, so a repeated <tool:data_write> merges onto the existing row instead of adding a second one. Read the last message `ts` processed for **[your bug-report channel]**, the rows for reports inside this tick's read window, and every row with an action still pending (`filter` on the pending column, `not_empty`), wherever its report sits in time.
- <tool:slack_read_history> with the channel id, `oldest` set to the earlier of two points: that timestamp minus **[a short overlap]**, and now minus **[your follow-up window, e.g. a day]**. On the first run there is no ledger row yet, so `oldest` is now minus the follow-up window. Then `limit=100`, and `cursor` until there is no next page. `oldest` is exclusive and windows the read server-side. The overlap is deliberate: a tick that failed halfway loses nothing, and the ledger absorbs the repeats. The follow-up window brings reports filed earlier back into the read, so their replies can be checked.
- **`not_in_channel` means the app is not a member**, and a read hits it before any post does. <tool:slack_join_channel> and retry once, for the reads here and the posts in step 7 alike. A private channel can't be joined through the API: a person has to invite the app.
- **History returns top-level messages only.** A parent with `reply_count` above zero hides its replies, and the replies are where repro steps, screenshots and "same here" land. <tool:slack_read_thread> with the parent's `ts` (a reply's own `ts` is refused), following `next_cursor`: pages run newest to oldest.
- **The dedup key is the channel id plus the message `ts`.** Bot posts (a `bot_id`) are skipped unless they come from **[your intake integrations]**, such as a bug form or a support tool posting as a bot: those are reports, and skipping every bot drops them silently.
- **The run's own posts are recognized by their text, not by their author.** <tool:slack_post_message> posts as the person whose Slack token the connector holds whenever a user token is set, so the run's replies carry that person's `user` and no `bot_id`, and skipping that user would drop the reports they post themselves. Every message the run posts opens with **[a fixed marker, e.g. "Bug triage:"]**, and a message opening with it is skipped, in history and in threads alike. That holds even when the post landed and its ledger write did not.
- **A key already on a row takes the already handled exit** when nothing on the row is pending and the parent's `latest_reply` is no later than the one stored (or both are absent). A row with a pending action goes straight to step 6 to finish it, with the outcome and band stored on it rather than a fresh classification; new replies on that report wait for the next tick.
- **A moved `latest_reply` is a follow-up.** <tool:slack_read_thread> with `oldest` set to the stored `latest_reply` returns only the replies added since. If none are left after the skips above, store the new `latest_reply` with <tool:data_write> and take the exit. Otherwise it depends on the row. A row carrying an issue skips steps 2 to 5 and goes to step 6 as a follow-up on that issue. A **not a bug** row has no issue, so the report goes back through step 2 with its whole thread: a reply can turn a question into a real failure.

## 2. Label what the report leaves out
Extract, in the reporter's own words wherever possible:
- **The symptom**: what happened, not what they think caused it.
- **Steps to reproduce**, if given.
- **Environment**: browser, device, app version, account.
- **Frequency**: once, intermittent, every time.
- **Impact**: blocked entirely, or working around it.

Where repro steps or environment are missing, write "not provided" in the issue and add the **[needs-repro]** label. A report with no repro is still worth filing, but it has to say so visibly: "cannot reproduce" and "was never told how" lead to opposite decisions when someone later considers closing it.

A how-to question, a feature request, or praise takes the **not a bug** exit, left for a person. A request dressed as a bug ("it should also export to X") is not a bug; a feature that exists and fails is.
- <tool:data_write> on that exit, the ledger row: the report key, the outcome **not a bug**, its class (question, feature request, praise) and the parent's `latest_reply` as read. Without the row, every tick inside the follow-up window would classify the same message again. A report sent back here by a follow-up updates the same row: only its new `latest_reply` if the thread still shows no bug, or the plan step 6 writes if it now does.

## 3. Title it on the symptom
Reporters routinely supply a cause along with the effect, and the cause is often wrong. "The cache is broken so my dashboard is empty" is one observation and one guess. The title is the observation: "Dashboard shows no data after sign-in". The guess goes in the body, attributed to the reporter.

A title stating a diagnosis sends the issue to the team that owns the guessed component, where it is correctly closed as not-a-bug while the real problem stays open.

The description, in this order: the symptom as a quote, steps to reproduce, environment, frequency, impact, the reporter's suspected cause, the account, and the source (reporter and a link to the Slack message). Build the link the same way every time, `https://[your-workspace].slack.com/archives/<channel id>/p<ts with the dot removed>`: step 6 searches for that exact string to recognize an issue or a comment this report already produced.

## 4. Catch duplicates and regressions
- <tool:linear_issue> with `op="search"` and `query`. **The search only matches the query as one exact string**, word for word and in order, somewhere in a title or description. There is no word-level matching: the same phrase with one middle word dropped finds nothing, so a full phrasing of the symptom almost never matches. Never search one.
- **Search short, distinctive terms, one per call**: the surface or feature noun ("export", "invoice PDF"), one key symptom word ("blank", "timeout"), any exact error text copied from the report, and both the reporter's word and the engineer's word for the same thing ("sign-in" and "auth"). Page each search with `first` and `after` until there is no next page: a common noun fills more than one.
- **What the search can't do**: it doesn't look up identifiers, takes no state filter, and leaves archived issues out. Linear archives closed issues automatically after a period each team sets, so keep **[your regression window]** shorter than that period, or an old fix is invisible to this step.
- **Read each hit's `state.type`, never its `state.name`.** Names are whatever a workspace renamed them to; the type is fixed. Open is `triage`, `backlog`, `unstarted` or `started`. Closed comes in three types: `completed`, `canceled`, and `duplicate`, which is a type of its own, not a kind of canceled.
- **Recently closed means `completed` with `updatedAt` inside the regression window, and `updatedAt` is a proxy.** The issue as returned carries no completion date, and `updatedAt` moves with any later edit (a relabel, a bulk move), so an old fix can read as recent. The proxy errs toward calling a regression, which costs one band, a label and a comment on the old issue, never a lost report: a regression is filed as a new issue either way.
- **A non-empty result is not a match.** Short terms bring back pages of loosely related issues. Read each candidate's description and accept only the same symptom on the same surface. Conclude **new** only when none of the short-term searches brought back a hit with that symptom. When a near miss names another issue as the place the work lives, fetch it with <tool:linear_issue> `op="get"`, which takes the identifier as written, before concluding.
- **A `duplicate` hit is never a match target.** Linear keeps the issue it duplicates as a relation, and these tools don't return relations. Look through the other hits from the same terms for one with the same symptom, and match on that. If none fits, <tool:linear_comment> with `op="list"` on the duplicate: a comment naming another issue's identifier resolves with `op="get"`. If that fails too, treat it as a **canceled match**: a person already judged it covered elsewhere, and step 6 asks them to follow the pointer.
- **Canceled is not a regression.** Someone decided, and a new report doesn't overturn that on its own. Nothing is refiled: step 6 comments on the canceled issue and flags it for a person.
- The outcome for the next steps: an open match is a **duplicate**, a completed match inside the window a **regression**, a canceled one a **canceled match**, no match **new**. **An open match wins over any closed one**: the second report of a regression the run already filed is a duplicate of that new issue, not another regression. A completed match outside the window counts as **new**, and its identifier is cited in the new issue's description. Step 5 sets a band only for new issues, duplicates and regressions.

## 5. Set severity from impact, not tone
Severity comes from what was described, never from the reporter's tone. An angry report about a misaligned button is not urgent; a polite note about an export with wrong figures is.
- **Top band**: blocked with no workaround, or data lost or shown wrongly.
- **Middle band**: a workaround exists but has to be performed repeatedly.
- **Low band**: cosmetic, or one unusual configuration.

Two facts each raise the band by one, and a raise stops at the top band: **[your account threshold]** distinct accounts reporting it, and **a regression**.
- **An account is the customer a report is about, not the person posting it.** A support or sales teammate posting for a customer counts that customer, and a "same here" in the thread counts the customer its author speaks for. When no customer is named, the poster's Slack user id stands in. One customer reporting twice is one account.
- **The running count on an existing issue comes from the ledger.** <tool:data_rows> filtered on that issue's identifier returns every row this process has linked to it, each with the accounts it counted. An issue a person filed has no row for its original report, so that report counts as one more account, unless its description names one already on the list.

The band becomes Linear's `priority` field:
- **Top band**: `1` (Urgent).
- **Middle band**: **[`2` (High) or `3` (Medium)]**, one value chosen once for your workspace.
- **Low band**: `4` (Low).

**`0` means no priority, not the lowest.** Writing `0` for the low band erases the signal, and when comparing with an existing issue a `0` ranks below `4`. On a duplicate, recompute the band with the new account counted, and only ever raise the existing issue's priority, to a smaller non-zero number: never lower one a person set.

## 6. File it on the owning team's board
- <tool:data_rows> on your ownership map: one row per **[product surface]** with the Linear team that owns it.
- <tool:linear_team> with `op="list"` for the team ids. State ids belong to a team, so leave `state_id` off on create and let the issue land in that team's default state.
- <tool:linear_label> with `op="list"` and no `team_id`, paged with `first` and `after`, to resolve **[needs-repro]**, **[regression]**, **[routing unclear]** and **[needs-decision]** by name. A list scoped to a team misses workspace labels, which have no team. When a name exists twice, take the label on the owning team or on no team: another team's label can't go on this issue.
- **A label that doesn't exist is not created mid-run.** Write the marker as the first line of the new issue's description instead ("Needs repro", "Routing unclear", or "Regression" followed by the closed issue's identifier). A missing **[needs-decision]** becomes the first line of the comment on the canceled issue. Name the missing label in the output so a person can add it once.
- **Plan on the ledger before the first Linear write.** <tool:data_write> the row: the report key, the outcome, the band, the team, the accounts counted, the parent's `latest_reply` as read, the target issue's identifier when there already is one, the time of the plan as a Unix timestamp (the form Slack's `oldest` takes), and every action the outcome needs, each marked pending. A regression's plan is the new issue, the comment on the closed issue, the thread reply and, in the top band, the alert. Each action is marked done, with the identifier or `ts` it produced, the moment its call returns. A crash anywhere in the sequence leaves the rest pending, and step 1 brings the row back here on the next tick.
- **A pending action is checked before it runs**, because a run can die after a call lands and before its done mark is written:
  - **The new issue**: <tool:linear_issue> with `op="search"` and `query` set to the report's Slack link exactly as step 3 builds it. A long unique string is what exact-string search matches reliably. A hit is this report's issue: take its identifier and skip the create.
  - **A comment**: <tool:linear_comment> with `op="list"` on the target issue, paged with `first` and `after`. A comment already carrying the report's link (for a follow-up, the link to the newest reply it covers) was posted.
  - **A priority raise or a label**: <tool:linear_issue> with `op="get"`, then raise only if the priority is still lower, and add the label only if it is missing.
  - **The thread reply and the alert**: checked as step 7 describes.
- **New or regression**: <tool:linear_issue> with `op="create"`, `team_id`, the symptom title, the description from step 3, `priority` and `label_ids`. A regression's description cites the closed issue's identifier and link, and a <tool:linear_comment> on the closed issue, carrying the new issue's identifier and the report's link, tells whoever fixed it. **Never silently reopen the closed issue**: its state records someone's decision, and a new issue carries today's environment and severity.
- **Duplicate**: <tool:linear_comment> with `op="create"` on the existing issue, carrying the reporter, the account, their quote, the environment and the Slack link. Then <tool:linear_issue> with `op="update"`, the `issue_id` and the new `priority`, only if the band went up.
- **Canceled match**: <tool:linear_comment> with `op="create"` on the canceled or duplicate-state issue, carrying the new report's specifics, the account, the Slack link and one line saying it was reported again (on a duplicate-state issue, a second line asking which issue it was merged into). Then <tool:linear_issue> with `op="update"` and `label_ids` set to the issue's current label ids plus **[needs-decision]**: `label_ids` replaces the whole set, so passing the new label alone strips the ones already there. State and priority stay as they are, since reopening is the person's call.
- **Follow-up** (from step 1): <tool:linear_comment> with `op="create"` on the issue on the row, carrying only what the new replies add (repro steps, environment, a screenshot link) and the link to the newest reply it covers, built as in step 3 from that reply's `ts`. A reply for an account not yet counted is a new account, so recompute the band and raise `priority` as on a duplicate. The plan for a follow-up stores the new `latest_reply`.
- **Ambiguous ownership**: file on the most likely team with **[routing unclear]** and one line naming the other candidate team. Never park it in a shared triage queue: an issue waiting on a routing decision is the most common way a real bug goes stale.

## 7. Close the loop in Slack
- <tool:slack_post_message> with `thread_ts` set to the report's `ts`, opening with the marker: the issue identifier and link, "linked to an existing issue" when it was a duplicate, "an earlier issue was closed on purpose, a person will decide whether to reopen it" on a canceled match, and what is missing ("no steps to reproduce: can you add them in this thread?"). Step 1 picks up the answer on a later tick. A follow-up gets no reply.
- **The top band only**, one message in **[your incident channel]**, opening with the marker: the symptom, the accounts affected, whether it is a regression, the issue link and the report's Slack link. Routine reports never go there: a channel that carries every report trains everyone to ignore the one that mattered.
- **A post is looked for before it is retried**, because a message can be deleted but not edited, and a post that errored or was left pending may have landed. For the reply, <tool:slack_read_thread> on the report with `oldest` set to the `latest_reply` stored in the plan (the whole thread when there was none), looking for a reply that opens with the marker and carries the issue identifier. For the alert, <tool:slack_read_history> on the incident channel with `oldest` set to the plan's time, looking for a message that opens with the marker and carries the report's link. Found means marked done with its `ts`; not found means post.
- `not_in_channel` on a post is handled as in step 1: <tool:slack_join_channel> and retry once.
- The reply moves the parent's `latest_reply`. On the next tick step 1 reads the new reply, skips it for its marker, stores the new `latest_reply` and exits: one thread read, and no comment.

## Output
Per tick: reports read, already handled, filings resumed from an earlier tick, follow-ups added to filed issues, not a bug, new issues by team and band, duplicates linked, regressions identified, canceled issues flagged for a decision, reports missing repro steps, ownership flagged as unclear, top-band alerts posted, and any triage label missing from the workspace (written as a description line instead).