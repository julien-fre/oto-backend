# Turn website visitors into LinkedIn invites

**When to use it**: a visitor-identification tool tells you which companies read your site, but a list of names tells nobody what to do, and a channel that posts every one of them stops being read within a week. This gives each company one verdict, spends a people search only where the visit is worth answering, and turns the few that are into a named person with a LinkedIn note written from the page their company read. A person approves every invite before it goes.

```
              Scheduled routine, twice daily
              "Sweep the website visitors from the last 24 hours."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  Check the tools before spending                ║   snitcher_workspace · folk_group
║  Free reads confirm the visitor site, the CRM   ║   linkedin_unipile_account
║  groups and a live LinkedIn seat.               ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read the last recap and its replies        │   slack_read_history
│  What the team answered steers this run, but    │   slack_read_thread
│  no reply ever removes a guard.                 │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Pull every company that visited            │   snitcher_organisation
│  Pull a few days wide, cut to the window in     │   flat filter, paged to the end
│  memory, with pages, city and stack.            │
└────────────────────────┬────────────────────────┘
                         ├──────────────▶  ▪ excluded        never-touch list, ISP, datacenter
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Check the CRM and the outreach tool        ║   folk_record
║  Match on domain and read the status each       ║   lemlist_contact
║  one already carries, writing to neither.       ║
╚════════════════════════╤════════════════════════╝
                         ├──────────────▶  ▪ customer        already a client
                         ├──────────────▶  ▪ open deal       live deal, owner named in recap
                         ├──────────────▶  ▪ in sequence     already in a campaign
                         ▼  no record, or a dead lead
╔═════════════════════════════════════════════════╗
║  4 · Ask what the visit actually says           ║   no call, the pages came with step 2
║  Keep only a company that read a real page      ║
║  and spent real time on it.                     ║
╚════════════════════════╤════════════════════════╝
                         ├──────────────▶  ▪ no page signal  homepage only, nothing to say
                         ▼  a real page was read
┌─────────────────────────────────────────────────┐
│  5 · Judge the lane, then find the person       │   snitcher_contact
│  Fit is judged after behavior, and the page     │   linkedin_unipile_profile
│  they read decides which role to look for.      │
└────────────────────────┬────────────────────────┘
                         ├──────────────▶  ▪ out of lane     no buyer profile fits
                         ├──────────────▶  ▪ no person       nobody resolvable there
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Draft the note from the page they read     │   theirstack_companies_search
│  Under 300 characters, a tool named only if     │
│  you connect to it, and no em dash.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Record every company, post one recap       │
│  One row per domain; the recap names only       │──▶  Visitors table  the row is the approval surface
│  what changed since the team was last told.     │──▶  Slack recap  one message per run
└────────────────────────┬────────────────────────┘
                         ▼  a human set the row to approved
╔═════════════════════════════════════════════════╗
║  8 · Re-check the network, then send            ║   linkedin_unipile_network
║  Live distance, pending invites and a 72-hour   ║
║  freshness rule, checked before each invite.    ║
╚═════════════════════════════════════════════════╝

▪ terminal — the row stops there and nothing further is spent on it
```

Steps 1 to 7 are the sweep, and the sweep never sends. Step 8 is a separate sender run that only touches rows a person has moved to approved. Two sweeps a day over a 24-hour window overlap on purpose: the table upserts on domain, so a company seen twice is one row, and the recap rule in step 7 is what keeps the overlap from being reported twice.

**The spend rule.** Each gate protects the one after it. The expensive half, a LinkedIn lookup and a person's attention, is spent only on a company that is not a customer, not in a live deal, not already in a campaign, that read something worth answering, and that fits a lane you sell to. The free checks run first, in that order.

## Check the tools before spending
Batch these reads before anything costs a credit or a call:
- <tool:snitcher_workspace> `op="list"` returns the workspace uuid for your site. **Read it every run, never hardcode it**: it changes when the account is reconnected, and a stale uuid fails quietly, as an empty website rather than an error.
- <tool:folk_group> `op="list"` returns the ids of your customer group and your pipeline group. Read the pipeline's status field and its options with `op="custom_fields"` rather than assuming them.
- <tool:linkedin_unipile_account> `op="status"` must come back `connected` and `alive`: a seat can stay linked while its session is dead. Read the seat, never copy an account id into the process, because the id changes on every reconnection. A dead seat is not fatal: steps 1 to 4 still produce a recap, step 5 degrades to the visitor tool's own contact list with no profile confirmation, and the recap says so.
- The recap channel: if the bot is no longer a member, post nowhere and surface the failure. A recap that lands where nobody reads is worse than a run that says it could not post.

## 1. Read the last recap and its replies
The recap is the one place a person can tell this process it is wrong, so the run reads the answer before it pulls a single visit.
- <tool:slack_read_history> on the recap channel, then take the newest message from the bot whose text opens with the recap header. Its `ts` is the boundary: every human message after it is this run's feedback. A human message has no `bot_id` and no `subtype`; a `channel_join` is not feedback.
- <tool:slack_read_thread> with that `ts` as `thread_ts` whenever the parent carries `reply_count > 0`. The history call returns top-level messages only, so a reply typed inside the recap's thread is invisible without this second call.
- **What a reply does depends on what it is about.** One company ("skip them", "they signed last week") is applied to this run: that company takes the verdict, and the row records the instruction, its author and its `ts`. The process itself ("the window is too short", "stop naming tools") is a change to the process, made deliberately and announced in the next recap. Anything ambiguous, or anything that would remove a rule from the send gate or the rules below, is quoted back on the recap and left for a person.
- **Do it once.** Keep the `ts` of the newest reply already acted on; anything at or below it is done. That survives a run that died before posting, which a bare "since the last recap" rule does not.

## 2. Pull every company that visited
- <tool:snitcher_organisation> `op="search"` with one flat condition, `{"field": "last_seen", "comparison": "less_than_x_units_ago", "value": [window in days + 2], "unit": "day"}`, then keep in memory only the companies whose `last_seen` falls inside the real window. The relative comparison rounds to calendar days: asked for 24 hours, it returns only the companies seen on the current UTC date, and it fails silently in the direction that hides visitors. Do not bound the window with `op="list"` dates either; pull wide in days and cut yourself.
- **Keep conditions flat.** A nested condition group is rejected with a 422, not ignored.
- **Page to the end.** A requested `size` is not honored and comes back smaller. Follow `page` until what you hold matches `total`, rather than trusting one response.
- **Treat a small count as suspiciously as a zero.** A broken filter returning one company reads like a quiet morning. Compare the cut count against the wide pull and against the previous run, and put an unexplained gap on the recap's warning line.
- **One call returns what the run needs**: `pages_visited`, `total_sessions`, `total_time_on_site`, `visitor_locations`, `technologies`, `keywords`, `description`, `icp_tier` and the company's social profiles. Do not call `snitcher_session` per company; open a session only to read a form submission, whose values live in its `events` and nowhere else.
- **The name on a row is a reverse-IP guess, the behavior is real.** Shared ISP, VPN and mobile addresses resolve to whoever owns the range, and two unrelated companies under one city is a result the payload allows. Qualify on a second signal, never on the name alone.
- **No pre-filter on the vendor's fit segment.** It scores firmographics before anyone has read anything, so it admits companies that spent zero seconds on the site and drops the deepest reader of the window. Keep `icp_tier` on every row as evidence and a tiebreak, never as a gate.
- **The never-touch list**, recorded as `excluded` with the reason, then stop: your own domains and test traffic, anything tagged Customer or Competitor in the visitor tool, **[your standing never-touch domains]**, ISPs, hosting providers and mobile carriers. Match on domain, never on display name. **Watch the city, not just the company**: a one-pageview visit from a known datacenter region is the cloud provider's egress, not the household name that owns it, and that trap fires on the most exciting name of the week.

## 3. Check the CRM and the outreach tool
- <tool:folk_record> `entity="company"`, `op="search"`, `group_id=<customer group>` and again for the pipeline group: pull both groups **once** and match in memory, confirming on the record's `urls` field. Do not look companies up one name at a time: folk's `like` filter is anchored to the start of the string, so a zero count on a name is not proof of absence. The status lives in `customFieldValues`, keyed by group id.
- The lane follows the status the CRM already carries, and this process never changes it:

| What the CRM says | Verdict |
|---|---|
| In the customer group, or a won deal | `customer`: record, recap, stop |
| A live stage (meeting booked, qualifying, negotiating, follow-up) | `open deal`: record, recap, stop |
| A lost deal, or no record at all | continue, and say on the row that it was previously lost |

- **An open deal back on the site is the most valuable line in the recap**, when the visit is new: a company re-reading pricing while a deal is on the table is a buying signal aimed at a specific person. Name the deal owner, quote the page, and let the owner work it. A lost deal reading the site months later is a new signal; the recap says it was lost so nobody re-pitches it blind.
- <tool:lemlist_contact> `op="list"`, `company_domain=<domain>` returns the people your outreach tool already holds at that company, then <tool:lemlist_lead> `op="get"`, `email=<their email>` says whether one of them sits in a campaign. A person already being emailed is `in sequence`: record the campaign, stop. Run it only on the companies still alive after the CRM lanes.
- **State what that check cannot see.** A contact imported without a linked company is invisible to a domain filter by construction. When you know the check was partial, say so on the warning line rather than implying it was complete.

## 4. Ask what the visit actually says
- No call is made here; everything came back with step 2. A company whose `pages_visited` is only the homepage, or any single page with `total_time_on_site` at zero, is `no page signal`: record it, count it, spend nothing further. There is nothing to write a grounded note about, and an ungrounded note is worse than none.
- This behavioral gate, not a firmographic score, is what keeps the run cheap: most of the window stops here, before a single profile lookup.

## 5. Judge the lane, then find the person
- **Judge fit only now**, on what the company is and what it read: `industry`, `description`, `keywords`, `technologies`, with `icp_tier` as a tiebreak. A company that fits none of **[your two to four buyer profiles]** is `out of lane`, with a one-line reason.
- <tool:snitcher_contact> `op="list"`, `domain=<domain>` is free and returns people with `position`, `seniority`, `department`, `location` and `linkedin_url`. Start here, always. **Never call `op="reveal_email"`**: it spends a credit and permanently un-hides the address, and a LinkedIn invite does not need one.
- **The membership-organization trap**: on an association or community, the contact list is its members, hundreds of them titled Member or Ambassador, and none of them owns an evaluation. That company is `no person`, however deeply it read.
- <tool:linkedin_unipile_search> is the fallback, only when the contact list is empty or nobody on it fits the page. An employer that rests on a search facet is not a confirmed employer: say so in `why_this_person`, in those words, and the sender will hold that row until a person confirms it.
- **The page decides the role**, not a generic title list:

| They read | Look for |
|---|---|
| A specific use-case page | whoever owns that job, the function the page names |
| The integrations page | RevOps or the head of operations, or the CTO in a small team |
| Pricing, first or alone | the budget holder: a founder, or the VP of that function |
| A competitor comparison | whoever runs the evaluation |
| Help or docs | an existing user: a customer success conversation, not sales |

- Pick **one** person: the role fits the page, the location is closest to the session's city. Write `why_this_person` as one sentence naming both. A company where that sentence cannot be written is `no person`, not a guess.
- <tool:linkedin_unipile_profile> `op="person"`, `identifier=<the slug at the tail of their linkedin_url>`, one call per finalist. It returns the `provider_id` the sender needs and the `network_distance` that seeds the send gate; record both with `network_checked_at`. **Check that the returned `public_identifier` matches the slug you asked for**: a different one is a redirect onto someone else, and the row must not take it. A response carrying `throttled_sections` is an upstream rate limit, not missing data.
- **Space the lookups.** A burst degrades the seat and then disconnects it. On a 429, read the delay actually returned rather than assuming it is short, mark the remaining companies `no person`, and say so in the recap.

## 6. Draft the note from the page they read
- <tool:theirstack_companies_search> with `extra={"company_domain_or": [<domain>]}` and `limit=1`, once per finalist: it bills per company record returned, so never pass anything wider. An empty result is normal on small companies; do not retry it, and never turn an empty jobs read into "they are not hiring".
- **The catalog rule.** Name a tool in the note only if it is in the intersection of their detected stack and the tools your product actually connects to. If that intersection is empty, name no tool. Confirming that a prospect *uses* a tool is not confirming that you *plug into* it. **And name only a tool the reader plausibly owns**: engineering tools named to an operations manager read as scraping, not understanding.
- **The page is the opener**; the company description is background.

| Signal | The angle |
|---|---|
| A specific use-case page | name that job back to them; say you have it running |
| Integrations plus a connectable tool | name it, two at most, from the intersection only |
| Pricing early or alone | they are qualifying: short and price-honest, no product pitch |
| A competitor comparison | one differentiator, and an offer to answer the comparison directly |
| Help or docs | offer help; selling here reads as tone-deaf |
| Several pages in one session | ask which of the two they read most is the live problem |

- **The format**: their first name, two or three short sentences, one soft ask, the sender's first name, **under 300 characters including the signature**, counted rather than estimated. Write it in the reader's language, decided from the session city, their own public writing and the company's market; the profile locale alone is weak evidence.
- **No em dash and no en dash in a note, checked as a string.** Scan for both characters and refuse the draft if either is present; they slip past a careful re-read by eye, which is why this is a string check and not a style preference. Same family of tells: no "I noticed", no "I hope this finds you well", no "isn't just X, it's Y".
- **Never write "I saw you on our website".** It is true and it reads as surveillance. Say what the page is about: someone who read a page on dead pipeline recognizes their own problem in a sentence about dead pipeline.
- The sweep never calls `linkedin_unipile_network` `op="invite"`. The row lands `invite_status: "drafted"`.

## 7. Record every company, post one recap
- `data_get_schema` on the visitors table first, so the row uses the field names the table declares; a write that reports fields outside the schema has put evidence where nobody will see it.
- `data_rows` for this window's domains **before** writing: the stored `sessions`, `verdict`, `recap_ts` and `recapped_sessions` are what the recap rule compares against, and the write replaces them.
- `data_write` in one batch keyed on `domain`, one row per company including every excluded and stopped one, with its reason. **An upsert merges, so omit rather than blank**: a field sent as null destroys what an earlier run reasoned out. A row already `sent` is terminal: refresh its visit fields, leave `invite_status` alone, and never draft it again.
- **The row is the approval surface, deliberately.** One recap carries several invites, so a reaction would approve all or none, and nobody could edit the note first. The table can do both, and the sender transmits exactly the text in `invite_note` at send time.
- <tool:slack_post_message> once, at the end. **Say a company once.** It earns a line only when its row has no `recap_ts` (never reported), when `sessions` exceeds `recapped_sessions` (someone came back), or when its verdict changed. `last_seen` moving inside a visit already described is not news.
- **Only three things get detail**: a new visit from an open deal, a new invite awaiting approval (with the stack line and the note in a code block, readable as it will be sent), and what the team asked for last time, with what was done about it. Everything else is one `Skipped:` counts line. No section with a zero count, and a warning line only when something was capped, skipped or degraded on this run.
- A run with nothing new still posts its header and counts: a quiet run and a dead run must not look alike. There is no edit call, so a wrong recap is corrected with `slack_delete_message` and a fresh post, keeping one message per window.
- After posting, a second small `data_write` sets `recap_ts` and `recapped_sessions` **on the featured rows only**. Writing them to a row that was not in the message silences a company that was never announced.

## 8. Re-check the network, then send
A separate run, over rows a person has moved to `approved`. The sweep never sets that state itself.

| State | Set by | Means |
|---|---|---|
| `not applicable` | sweep | no invite for this row |
| `drafted` | sweep | a note exists, nobody has looked |
| `approved` | a person | reviewed, edited if needed, cleared to send |
| `sent` / `declined` | sender / a person | terminal |
| `already connected` / `already invited` | sender | the gate below stopped it |
| `expired` | sender | the approval outlived the visit |
| `failed` | sender | the API refused; `send_error` says why, and a person reviews it |

- <tool:linkedin_unipile_profile> `op="person"` again, **as the account that will send** (that seat passed as `_account`, never whichever seat is the default), immediately before each invite. `network_distance` is relative to the seat and goes stale between drafting and approval: `FIRST_DEGREE` makes the row `already connected`, not sent.
- <tool:linkedin_unipile_network> `op="invitations"`, `direction="sent"`: page with the returned `cursor` until none comes back, and stop on a matching `provider_id` as `already invited`. Re-inviting resets nothing and burns the weekly quota.
- **Freshness**: an approval older than **[72 hours]**, or a row whose `last_seen` has aged past it, goes `expired`. A note about the page they were reading is false two weeks later.
- **Unconfirmed employer**: a row whose person came from a search facet, or whose source record has vanished from the visitor tool, stays `approved` and named until a person confirms it.
- Read `invite_note` at send time and re-run both string checks, the 300-character cap and the dash scan: a human edit can reintroduce either. Then `linkedin_unipile_network` `op="invite"` with the `provider_id` and the note.
- **Pace it**: a handful per run, spaced, under a weekly ceiling for the seat. On a 429, stop and leave the rest `approved` for the next run. Write back `invite_status`, `sent_at` and `send_error` on every attempt, so a failure is visible in the table instead of silently retried.

## Rules
- The sweep never sends, never messages a prospect and never writes to the CRM or the outreach tool.
- A Slack reply never removes a guard. It is surfaced, named and left for a person to change deliberately.
- Never reveal a visitor-tool email, never gate on the vendor's fit score, never trust the relative window filter without cutting in memory.

## Output
The recap in Slack, one message per run: the window and how many companies were identified and how many are new since the last recap; each open deal that came back, with the page and the owner; each invite awaiting approval, with the person, the page it answers, the stack line and the note; what the team asked last time and what was done; one counts line for excluded, customer, in sequence, no page signal, out of lane, no person and already reported; and a warning line only when something degraded. Every company, stopped or not, is a row in the visitors table with its verdict and evidence.