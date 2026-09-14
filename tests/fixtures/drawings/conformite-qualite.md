# Weekly GitHub check for leaked secrets and failing CI

**When to use it**: you want guardrails on data, secrets, traceability and quality without slowing the team down. One scheduled pass a week reads your repos, your CI and your policy register, turns every real gap into a tracked issue, and fixes nothing itself.

```
              Scheduled routine, weekly
              "Run this week's security and data hygiene check across our repos."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Scope the week                             │   github_repos
│  From a day before the last completed run;      │   data_rows
│  unseen scoped repos reported as not scanned.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Scan for committed secrets                 │   github_search
│  One whole-word code search per pattern; read   │   github_files
│  each hit to rule out placeholders.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Check CI and the review trail              │   github_actions
│  Failing default-branch CI, merges with no      │   github_search
│  approving review, and direct pushes.           │   github_pulls, github_repos
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Check the policy register is current       │   notion_get_database
│  Stale or ownerless policies; data rows with    │   notion_query_database
│  no legal basis or retention window.            │   notion_get_page
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Hold the bar                               ║
║  Keep only findings someone could act on —      ║
║  nothing is fixed, rotated or reverted.         ║
╚════════════════════════╤════════════════════════╝
                         ├──────────▶  ▪ dropped   not actionable or already resolved
                         ▼  a real, actionable finding
┌─────────────────────────────────────────────────┐
│  6 · File or update the issue                   │   linear_issue, data_rows
│  Match the key in the ledger, not in search;    │   linear_comment, data_write
│  a closed issue re-files only on new evidence.  │   github_actions
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Post the weekly report                     │   slack_post_message
│  One message even on a clean week, with no      │   data_write
│  secret in it; then the run is marked done.     │
└─────────────────────────────────────────────────┘

▪ terminal — the finding stops there and no issue is filed
```

## 1. Scope the week
- <tool:data_rows> — read the run row of **[your findings ledger]**: a datastore you create once, with `dedup_key` declared as its key, holding one row per finding (step 6) and one run row (step 7). The window opens a day before the start of the last run that completed; on the very first run, **[your first-run lookback]** before today. A skipped or failed week then widens the next window instead of leaving a gap, and the day of overlap costs nothing because step 6 never files a key twice. Fetch today's date at run time: every "since" and "overdue" below is computed from it, never hardcoded.
- <tool:github_repos> — list your organization's repositories sorted by last push, 100 to a page until a short page comes back, minus what **[your scope list]** excludes (archived repos, forks you don't ship). Keep each repo's `default_branch` from the listing; step 3 filters on it by name. Two scopes come out of this: every visible scoped repo for the CI check, and the ones pushed inside the window for the review-trail and direct-push checks.
- **[your scope list]** also names the repos that must be covered. The organization listing only returns repos the token can see: a repo it has no access to is simply absent, with no error. So compare the full listing against that list and report every scoped repo missing from it as **not scanned (no access)**. A repo you couldn't read is never a clean repo.
- The secret scan in step 2 covers the whole organization every week, since a pattern you add can surface a key committed long ago; step 6 keeps old hits from being filed twice.

## 2. Scan for committed secrets
- <tool:github_search> — code search, **one query per pattern across the whole organization** (the org qualifier plus a real term), not one query per repo: code search has its own rate limit, far lower than the rest of the API, and a per-repo loop stalls on any organization of real size. Run the queries one after another from **[your pattern list]**, 100 results to a page.
- Write every pattern as whole words. This search matches whole tokens only: it drops punctuation and has no prefix, wildcard or regex matching. A query for the first characters of a key finds nothing when those characters run straight into the key body, and a pattern that can never match looks exactly like a clean one. Patterns that do match: the words of a private key header, the variable or setting names keys are usually assigned to, token prefixes that end at a hyphen, and environment files matched by filename plus a real term such as a setting name.
- Say in the report what this search can't see: only the default branch is indexed, very large files aren't, and history isn't searched at all. A secret committed and then deleted still sits in the history and still works. And a key whose prefix is fused to its body, as with most cloud access key ids, can't be found by code search at all: leave those to the history or secret scanner in your CI and list them in the report as **not covered**. This check catches what's on the default branch today; it doesn't replace that scanner.
- The tool flags a truncated response: the result cap was hit, or the search gave up midway. When it does, narrow the query by repo or path and run it again. If it's still truncated, report that pattern as **incomplete**, never as clean.
- <tool:github_files> — code search doesn't return file contents, so read each hit to tell a real value from a placeholder, an example in the docs, a test fixture, or a line that only names the variable. Record repo, path, line, pattern type and a masked value: the first four characters plus the length. For a value under about 20 characters, record only the pattern type and the length, because four characters are a real share of a short password. On a long token the first four are usually just its known prefix and give nothing away. **Never copy the value itself** — not into the issue, not into the ledger, not into the report. The tracker and the chat channel are read by more people than the repo.

## 3. Check CI and the review trail
- <tool:github_actions> — list the runs of **every visible scoped repo**, not only the pushed ones: a scheduled workflow can start failing in a repo nobody pushed to. Filter on the default branch by its name from step 1, 100 runs to a page. The tool takes no date filter and returns the newest runs first, so page until a run was created before the window opened. Group by workflow and take the most recent completed run of each. Read status and conclusion together, and classify the conclusion: failure, timed_out and startup_failure are findings; success is not; cancelled and skipped say nothing about whether the workflow passes, so look past them with a second call filtered on that workflow's file name; an empty conclusion means the run is still going, which is not a finding yet. Flag a workflow whose latest completed run failed, and note how many runs in a row it has failed. A workflow with no run inside the window isn't judged this week. Read only: never rerun, cancel or dispatch — a rerun can redeploy.
- A 404 on one of these per-repo calls almost always means the token lacks access to that private repo, not that it's gone. List it as **not scanned** in the report.
- <tool:github_search> — an issue search for pull requests merged in your organization since the window opened (the org, pull request, merged and merged-since qualifiers), 100 to a page until a short page. Asking for merged directly avoids the trap that a merged pull request is only "closed" in the pull request API. The search knows nothing of your scope list, so keep only results from the pushed in-scope repos of step 1.
- This list must be complete before the direct-push check uses it. When the tool flags the response as truncated, rerun the query once per pushed in-scope repo with a repo qualifier, one after another, since search has its own low rate limit. A repo whose query is still truncated gets its review and direct-push checks reported as **incomplete**, and nothing from those checks is filed for it: a merged pull request missing from the list would make its commits look like unreviewed pushes, and those false findings are specific enough to pass the gate.
- <tool:github_pulls> — list the reviews on each merged pull request, 100 to a page until a short page (the default page is 30, and a busy pull request can have more). Flag any with no submitted approval from someone other than its author. A review saved without an event stays pending and is visible only to its author, so it doesn't count. An admin merge that bypassed a required review shows up as exactly this finding. Merges from the automations listed in **[your allowed bots]** are exempt.
- <tool:github_repos> — a push that skips the pull request never shows up in that search. For each pushed in-scope repo, list the commits since the window opened (with no ref, this reads the default branch), 100 to a page until a short page. A commit is accounted for when it is the merge commit of one of this window's merged pull requests (read with <tool:github_pulls> op=get) or one of that pull request's own commits (op=commits, paged the same way). A rebase merge rewrites the SHAs but keeps each commit's author, author date and message, so match on those when the SHA doesn't match. Every commit left over is an **unreviewed push**, except those from **[your allowed bots]**. A pull request's commit list stops at 250: when one hits that cap, report that repo's push check as **incomplete** instead of flagging its leftovers. The since filter reads the commit date, not the push date, so a commit made long before it was pushed can slip past; say so in the report.

## 4. Check the policy register is current
- <tool:notion_get_database> — confirm each register holds a single data source. Under the Notion API version this connector uses, this call returns the database's data sources but not its properties, and the query tool below reads only the first source. A register with more than one source goes in the report as **incomplete**.
- <tool:notion_query_database> — read the schema off a row, before any filter: one query with no filter and a page size of 1. Every row comes back with every property of its source, typed, and present even when empty, so that one row shows the register's columns. Confirm each property the filters name still exists with the type the filter expects: a date for the review date, a person for the owner. A missing or retyped property puts that register in the report as **not checked**, never clean; a filter naming it would fail with a validation error anyway. An empty register is reported as not checked too.
- The quiet case is a property that still exists but was replaced by a new column: still there, no longer filled in, it skews the results without any error. Compare the property names on that row with the ones stored on the run row last week (step 7). Report any new date or person property as a possible replacement, without guessing which one is current.
- <tool:notion_query_database> — then query **[your policy register]** with one compound "or" filter: next-review date before today, next-review date empty, or owner empty. The empty-date branch is not optional: a "before" filter never matches a page with no date at all, so without it a policy nobody ever scheduled a review for would never be flagged. Query **[your data-processing register]** the same way for rows with no legal basis or no retention window — "no data collected without a legal basis and a retention period" is the rule that register exists to prove.
- This query takes a page size (100 at most) and no cursor, so it never reads past its first page. When a query returns a full page, or its response says more rows remain, report that register as **incomplete**, never as clean: the rows past the first page were never read.
- The returned rows are the source for each finding: they already carry every property, the owner and the last-edited time. Record the review date and owner as the finding's evidence. The review date is the source of truth, not the edit time: a review that confirms a policy doesn't have to change a word, so a page left untouched for a long time is not a finding on its own.
- <tool:notion_get_page> — a recheck, not a second read of the whole register. Filing in step 6 happens after the whole scan, so re-read each flagged row just before its issue is filed, and drop the finding if the review date or owner was updated in the meantime.

## 5. Hold the bar
The gate between checking and filing. A finding goes through only if a person reading nothing but the issue would know what to do next: which repo and file, which workflow, which merge or push, which policy and who owns it.
- **Dropped**: placeholders and documented example values, test fixtures, a failure already superseded by a green run, anything too vague to act on. Keep each drop and its reason for the report — a finding that never becomes an issue is still evidence the check ran.
- **Nothing is fixed here.** The run never rotates a key, deletes a file, reverts a merge, reruns a workflow or edits a policy page. A committed secret is remediated by rotating it, and a person does that — deleting the file alone leaves the key valid and in the history.

## 6. File or update the issue
- Every finding carries a stable dedup key, one unbroken string with no spaces: check type, repo, path and pattern for a secret (`hygiene:secret:<repo>:<path>:<pattern>`), repo and workflow file for CI, repo and pull request number for a merge, repo and commit SHA for a direct push, page id for a policy. Replace every character other than letters, digits, `.`, `/`, `:` and `-` with `-`. Paths carry underscores and asterisks, and the tracker stores descriptions as Markdown, which can escape them; a key whose stored form differs from the one the run computes would never match again. The key sits on its own line in the issue description, and under it an evidence line records what the issue was filed on: the masked value for a secret, the failing run and its streak for CI, the review date and owner for a policy.
- <tool:data_rows> — look the week's keys up in **[your findings ledger]**: one read filtered with `in` on `dedup_key`, paged with the cursor until none is returned. A ledger row holds the issue identifier and link, the latest evidence line, and the issue's state as last seen. **The ledger, not the tracker's search, is what remembers a finding.** <tool:linear_issue> op=search leaves archived issues out, and Linear archives closed issues automatically after a period each team sets. A hit a person closed keeps coming back, because step 2 scans the whole organization every week; once its issue is archived, a search would find nothing and the run would file it again.
- <tool:linear_issue> — for each key the ledger holds, op=get on the recorded issue and read its state type: completed or canceled means closed. When the call no longer returns the issue, treat it as closed as well: a person took it out of the tracker.
- **The ledger doesn't hold the key**: fall back to op=search on the key, paging with first and after until no next page remains. The search matches the text anywhere in the title or description, so a key that another key extends matches too: keep only an issue whose description carries this exact key on its own line. A match found this way (an issue filed by hand, or before the ledger existed) is added to the ledger and handled like the cases below. No match means no issue carries the key.
- **An open issue carries the key**: compare today's evidence line with the ledger's. <tool:linear_comment> — comment with the new evidence line only when they differ, such as a longer failure streak; otherwise leave the issue alone and count it as still open.
- **No issue carries it**: create one in **[your security team]** with a title that states the symptom ("Live API key committed in a config file", "Default-branch CI failing on the deploy workflow"), the key and evidence lines, the evidence itself with any value masked, the repo or page link, the owner if known, and the date first seen. Leave priority and assignment to a person. <tool:data_write> — write its ledger row right after creating it, before moving to the next finding, so a run that stops midway doesn't file the same key again next week.
- **Only a closed issue carries it**: closing was a person's decision — a key rotated but left in the file as a dead string, a hit judged not to be a secret. Detection alone never re-files it. File a new issue that references the closed one, instead of reopening it quietly, only when the evidence changed after that issue was filed:
  - a secret: today's masked value differs from the one in the ledger, so a different value now sits in that file. A new key of the same type and length masks the same way, which is one reason the report still lists these hits.
  - CI: <tool:github_actions> — list that workflow's runs on the default branch with a success status and a page size of 1, which returns its latest green run. Re-file only when that run is newer than the failing run the ledger recorded: the workflow was fixed after the issue and has broken again.
  - a policy: the review date differs from the one in the ledger, meaning someone moved it, and the new date is overdue again.
  - a merge or a direct push: never re-filed once any issue has carried its key.
- A re-filed issue takes over the key's ledger row, which keeps the closed issue's link. Every closed match that doesn't meet those conditions goes to the report as **closed but still detected**, with the closed issue's link, so a person can see it without the tracker filling with duplicates week after week.
- <tool:data_write> — at the end of the step, the remaining ledger changes in one batch with `key="dedup_key"`, so each key updates its own row instead of adding a second one: the state just read, the current issue link, and the new evidence line for an open issue. A closed issue's row keeps the evidence it was closed on, because that line is what the re-file conditions compare against.

## 7. Post the weekly report
- <tool:slack_post_message> — one message to **[your security or engineering channel]**, addressed by its channel ID: new findings with their issue links, findings still open from earlier weeks, findings closed but still detected, and what couldn't be checked (repos not scanned, patterns with a truncated search, key types left to the CI scanner as not covered, repos with an incomplete review or push check, registers not checked or incomplete, possible replacement columns). Post even on a clean week — a one-line "nothing new" is what tells a quiet week from a broken run. Link issues instead of restating evidence, so no secret, masked or not, ends up in the channel.
- Write the message once, from the finished run. A Slack message can be deleted but not edited, and a long one is split into threaded parts rather than truncated — don't post it again thinking it was cut.
- <tool:data_write> — only once the post has gone through, update the run row: this run's start time, and the property names read from each register in step 4. Written last on purpose: a run that stops before this point leaves the previous run row in place, so next week's window reaches back over it.

## What this never does
- Never fixes, rotates, reverts, reruns or edits anything. It files issues and reports; people remediate.
- Never puts a secret value in an issue, a comment, the ledger or a message.
- Never reopens or re-files a closed issue on detection alone.
- Never reports a check it couldn't run as clean — missing is reported as missing.

## Output
Report: findings filed per theme (secrets, CI, review trail and direct pushes, policy and data registers), findings still open from earlier weeks, findings closed but still detected, findings dropped at the gate and why, and every repo, pattern or register that couldn't be checked, came back incomplete, or isn't covered by this search, plus any register column that looks like a replacement.