# Merge HubSpot duplicates and track contacts who changed jobs

**When to use it**: every CRM drifts the same two ways. Enrichment, list building and imports each create companies by domain, so one company ends up as two records with its deals and contacts split between them; meanwhile a champion changes jobs and their record keeps pointing at an employer they left, which nobody notices until an email bounces. Both are matching problems with public evidence, and both need a weekly pass rather than a quarterly clean-up. This one plans company merges for the CRM owner to approve in Slack, follows contacts who moved, and posts one recap a week.

```
              Scheduled routine, weekly
              "Run the CRM hygiene pass on everything changed in the last 90 days."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Check the key and read the ledger          ║   hubspot_object, data_rows
║  One free read proves the CRM answers; this     ║   one row per ISO week
║  week's row and the open recaps are loaded.     ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no CRM access    one honest line posted, no counts
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · The CRM owner approves the merges          ║   slack_read_thread
║  Read on a later run: only the owner's explicit ║   silence approves nothing
║  reply in a recap's thread approves a group.    ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ refused          excluded from later runs
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Note the winner, hand the merge over       │   hubspot_object, slack_post_message
│  Approved groups re-read, the losers' values    │   the merge stays the owner's click
│  noted on the winner, the owner told in thread. │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already posted   this week's recap is already out
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Pull companies changed in the lookback     │   hubspot_object
│  Every company modified in the window, with     │   paged by after, 100 per page
│  its domain, dates, deals and contacts.         │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no domain        never grouped, never merged
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Group by domain, pick the winner           │   deals x 2 + contacts
│  Same normalized domain; the winner has the     │   subdomains stay distinct
│  most deals and contacts, then the newest.      │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ unique domain    one company, nothing to merge
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · Re-read each group before planning         ║   hubspot_object
║  Companies fetched fresh; a changed domain,     ║   a merge cannot be undone
║  an exclusion or an open approval skips it.     ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ skipped          reason written to the recap
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Check contacts against their profiles      │   hubspot_list
│  This week's slice of contacts, each dated      │   linkedin_unipile_profile
│  position compared with the CRM company.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ candidate only   listed for a person, not acted on
                         ▼  a confirmed move
┌─────────────────────────────────────────────────┐
│  8 · Record the confirmed moves                 │   hubspot_object
│  Notes on the old contact and account; the      │   notes, never an overwrite
│  person added where they work now.              │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  9 · Write the ledger, then post the recap      │──▶  Slack recap  every week, even a quiet one
│  One row per week first; one message with the   │   data_write, slack_post_message
│  numbered groups and the moves in its thread.   │   reply with numbers to approve
└─────────────────────────────────────────────────┘

▪ terminal — the run, group or contact goes no further from there: nothing more is noted, created or merged in the CRM for it, and it is still counted in the ledger and the recap
```

## Rule zero: a wrong merge is forever
A merge cannot be undone, so everything here leans toward leaving two records alone when the evidence is thin. Companies are only ever grouped on the same normalized domain, never on a name. Every group is re-read before it is planned and again before the note that precedes its merge. The run itself never merges: it plans, the CRM owner approves group by group in the recap's thread, a later run writes the losers' details onto the winner, and the merge is the owner's click. The only writes the run makes on its own are notes and a contact added at a company already in your CRM, all of them reversible.

An approval therefore always lands on a later run than the recap that asked for it: next week's scheduled run, or a run the owner starts sooner once they have replied ("the merges are approved, note them now"). Both enter the same way. Steps 2 and 3 act on every approval given since the last run, and a run in a week whose recap is already out stops after step 3 instead of scanning and posting a second time.

Two things a broader clean-up usually includes are left out on purpose: reformatting fields (countries, phone numbers, placeholder values) and merging duplicate contacts. Both overwrite values a person typed, and a contact merge is as final as a company merge, so they belong in a separate pass with their own review.

## 1. Check the key and read the ledger
- <tool:hubspot_object> — `op="search"` on `companies` with `limit=1`, a free read that proves the key answers before anything else runs.
- **If HubSpot does not answer**, post one line to **[your CRM data channel]** saying the pass did not run, with the error as HubSpot returned it, and stop. Keep that line's `ts` on the week's ledger row as `failure_ts`, never as `recap_ts`, with the counts left empty: a rerun the same week, once the key answers, still posts the real recap. Never invent a count. The recap posts every week for the same reason: a week without one must mean the job did not run.
- <tool:data_rows> — **[your hygiene ledger]** holds one row per ISO week, keyed on `run_key` (`<YYYY>-W<ww>`), so every run inside a week reads and updates the same row. Two reads: this week's row, and the open recaps, meaning rows with a `recap_ts` that still hold a group `approved` or `noted` (whatever their age), or a group `pending` (the last **[4]** weeks only). **This week's row with a `recap_ts` means the scan and the recap already ran**: steps 2 and 3 still run, because that is how an approval given since gets acted on, and the run stops after step 3.
- Settle three things before reading anything else, and record them on the row: the lookback (**[default 90 days]**), the exclusions (domains the CRM owner wants left alone, plus the group keys refused in an earlier thread), and any subdomain the owner explicitly folds into its parent. None by default.

## 2. The CRM owner approves the merges
- Nothing in the recap's own run waits for a reply: approvals are read here, on a later run.
- <tool:slack_read_thread> — for each open recap: `channel` the channel id, `thread_ts` the row's `recap_ts`, `oldest` the row's `last_reply_ts` (the `recap_ts` when there is none), following `next_cursor` while `has_more` is true. Pages come newest first; apply decisions oldest first, so a later reply overrides an earlier one.
- **Keep only the owner's replies**: the reply's `user` must be **[the CRM owner's Slack user id]** (looked up once with <tool:slack_find_user_by_email> and kept in the process settings), and its `ts` must not be in the row's `own_ts`. The run posts as the connected Slack account, so a `bot_id` test cannot tell the run's own thread lines from a person's, and those lines name every group. When the connected account is the owner's own, `own_ts` is the only thing that stops the run approving itself.
- Only an explicit reply naming groups decides them (`merge 2, 5`, `keep apart 3`). Numbers restart with every recap, so a number is resolved to its group key through the row whose thread it sits in, then applied to that key on every row where it is still open. An approved key becomes `approved`; a refused key becomes `refused` and joins the exclusions, so the same pair is not asked about again. Anything ambiguous stays `pending` and is quoted back in the next recap; silence approves nothing.
- <tool:data_write> — write the new statuses, and the newest processed reply's `ts` as `last_reply_ts`, on every row read, so a reply is never acted on twice by a rerun.
- A pending group on a recap older than **[4]** weeks is not read again. It comes back on its own: as long as the duplicate exists, the weekly scan lists it again under a newer recap.

## 3. Note the winner, hand the merge over
- Each approved group key is handled once, whichever rows it appears on.
- <tool:hubspot_object> — `op="get"` on every company of the group once more, since days may have passed, with the same property list for all of them. A company that is gone or no longer carries the group's normalized domain makes the group `skipped`, with its reason.
- Then `op="add_note"` on the winner: each loser's id, name and domain, and every property where two records hold different non-empty values, with both values. Write it before anyone merges. Do not rely on memory of which value a merge keeps: the note is what preserves the value that gets discarded, and it is what lets a bad merge be traced afterwards.
- <tool:slack_post_message> — one reply in the thread where the approval sat (`thread_ts` that row's `recap_ts`): the group, that the note is on the winner, and the loser ids to merge into it, richest loser first. Add the reply's `ts` to the row's `own_ts` and mark the group `noted`. A skipped group gets the same reply with its reason instead.
- The CRM owner merges each loser into the winner from the winner's record. For every group `noted` on an earlier run, `op="get"` on the winner with `hs_merged_object_ids`: a group whose loser ids are all listed there becomes `merged` in the ledger; the rest stay `noted` and show in the recap as still open.
- **If this week's row already carries a `recap_ts`** (step 1), the run ends here.

## 4. Pull companies changed in the lookback
- <tool:hubspot_object> — `op="search"` on `companies`, filter `hs_lastmodifieddate` `GTE` now minus the lookback (epoch milliseconds in filters; results come back as ISO strings), sorted by that date, 100 per page, paging with `after` until no cursor comes back. A company created in the window was also modified in it, so one filter covers both.
- Ask for `name`, `domain`, `country`, `createdate`, `hs_lastmodifieddate`, `num_associated_deals`, `num_associated_contacts`. If a portal does not return the two counts, count associations with `op="associations"` only for the companies that end up in a group, never for the whole pull.
- **A single search stops at 10,000 results.** A window that reaches the cap is split into two date ranges and both are read, or the tail of the window silently never gets checked.
- A company with an empty `domain` is counted and never grouped: a duplicate without a domain is a person's call.

## 5. Group by domain, pick the winner
- **Normalize every domain the same way, and only this way**: lowercase; strip `http://`, `https://` and a leading `www.`; cut at the first `/`; strip whitespace and a trailing dot. Nothing else. `mail.example.com` and `example.com` are two domains unless the CRM owner folded that subdomain in step 1.
- Never group on free mailbox, shared-office, link-in-bio or website-builder domains: many unrelated companies sit on them, and a shared domain there is evidence of nothing.
- A group is two or more companies on the same normalized domain. The **winner** has the highest `num_associated_deals × 2 + num_associated_contacts`; on a tie, the most recent `hs_lastmodifieddate`. Linked history is the tiebreaker that matters: a missing phone number can be enriched again, months of deal history cannot be rebuilt, so the busier record wins even when it is the newer or scrappier one.
- Write each group down: its number in this recap, its **group key** (`<normalized domain>|<company ids, sorted>`, the identity approvals and exclusions attach to), the winner's id, name and score, and each loser's id, name and score. A domain with a single company is not a group. A group that gains or loses a company gets a new key, and is asked about afresh.

## 6. Re-read each group before planning
- <tool:hubspot_object> — `op="get"` on every company of the group with `domain`, `name`, `country` and `hs_lastmodifieddate`. The search ran minutes ago and a merge is forever, so the plan is made on fresh reads.
- A group is **skipped**, with its reason in its entry, when: a company no longer exists or no longer carries the same normalized domain (`domain changed`); the domain or the group key is on the exclusion list (`excluded by the owner`); its key is already `approved` or `noted` on an earlier row (`awaiting the owner's merge`, tracked by step 3 rather than asked again); a company already sits as a loser in an earlier group of this run (`already planned`); or the companies' countries differ (`possible parent and subsidiary`), because a group sharing a domain across countries is usually two legal entities that belong apart. A skipped group still appears in the recap.
- A group still `pending` from an earlier recap is not skipped: it is listed again under this recap's number, and a reply in either thread decides it.

## 7. Check contacts against their profiles
- <tool:hubspot_list> — `op="members"` on **[your contacts to watch]** (customers and open deals first, since a departing champion there is a retention signal well before the renewal), with `properties` for name, email, company, LinkedIn URL, last activity date and hard-bounce reason (internal names from <tool:hubspot_property>, never from the labels). Passing `properties` returns full rows in one memberships page plus one batch read, instead of a `get` per member that runs into the private app's 190 requests per 10 seconds around the fortieth contact.
- Take this week's slice: **[N contacts]** ordered by the last-checked date in **[your contact checks table]** (<tool:data_rows>), oldest first. Every contact comes round on a rotation, and the connected LinkedIn account is never burst.
- <tool:linkedin_unipile_profile> — `op="person"` on the profile slug (the slug, never a numeric member id), no more than a handful in parallel. **Check the returned `public_identifier` matches the one requested** before using it. A response listing `throttled_sections` has an empty experience section because of a rate limit, not because the person has no job: retry those contacts in a later catch-up pass instead of concluding anything.
- Compare the current positions with the CRM company, matching the employer by the domain of its company page (`op="company"`: company lookups have their own per-account quota, but a page read twice within a few hours is served from cache), not by name: legal suffixes and trading names make name matches unreliable both ways.
- **A mismatch is a candidate, not a fact.** A move is confirmed only when the profile no longer lists the CRM company as a current position, and either that position now carries an end date, the new one started after the contact's last activity in the CRM, or the work email has hard-bounced. People hold several current roles (an advisory seat, a board, a side project), so a new current position never confirms a move while the old one is still open. Anything short of confirmed goes to the recap for a person, and nothing is written in the CRM.

## 8. Record the confirmed moves
- <tool:hubspot_object> — `op="add_note"` on the old contact (where they went and roughly when, per their profile) and on the old company (a known contact has left, with their role). **Never overwrite the old contact's company, title or email**: the account's history stays intact and a person typed those values.
- Look the new employer up with `op="search"` on `companies` by its normalized domain. **When it is already in your CRM**, search its contacts for the same LinkedIn URL first, then `op="create"` the contact there (name, title, LinkedIn URL, associated to that company) with a note saying where they came from. The search first makes the step idempotent: a rerun finds the contact instead of creating a second one. Flag it in the recap as a warm introduction.
- When the new employer is not in your CRM, nothing is created: the move and the company go into the recap for the account owner to decide.
- <tool:data_write> — stamp the contact's last-checked date and outcome in **[your contact checks table]** either way, keyed on the contact id, so the rotation moves on and a candidate already reported is not re-announced as new.

## 9. Write the ledger, then post the recap
- <tool:data_write> — this week's row, keyed on `run_key`: lookback, exclusions, companies scanned, companies without a domain, groups found, groups skipped with reasons, contacts checked, moves confirmed, candidates, and the groups as JSON (each with its number, group key, winner, losers, scores, status and reason). **The row goes first**: if Slack fails, the week's plan is already kept and the post can be replayed from it.
- <tool:slack_post_message> — to **[your CRM data channel]** by channel id. The top message carries the counts in two or three lines and ends with the ask: reply in this thread with the group numbers to merge or keep apart, and a later run acts on the reply. Replies to that message, through `thread_ts`: one line per group (`<domain> → keeps <winner> (<id>, score) ← <loser> (<id>, score)`, each id linked to its record), the groups noted and waiting for the owner's merge, the merges confirmed since last week, then each confirmed move, warm introduction and unresolved candidate **named individually**. A week with nothing to report is the header alone.
- Write back onto the row immediately: `recap_ts` (the top message's `ts`), which step 1's replay guard and step 2's thread read both use, and in `own_ts` every `ts` the run posted (the `ts_all` of a split message and each thread line), which step 2 excludes when it reads replies.
- Posting and reading the thread both need the connected account in the channel, or they fail with `not_in_channel`: <tool:slack_join_channel> fixes that for a public channel; a private one needs a person to `/invite` the account. Text over about 4,000 characters is split into threaded parts rather than truncated; read `split_into` rather than posting again.

## Output
Report: approvals read and groups refused, groups noted on their winner and waiting for the owner's merge, merges confirmed, companies scanned, companies without a domain, duplicate groups found, groups skipped by reason, contacts checked this week, moves confirmed, warm introductions at companies already in your CRM, accounts that lost a known contact, and candidates left for a person.