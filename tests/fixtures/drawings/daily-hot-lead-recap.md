# Send each rep a daily Slack recap of hot leads

**When to use it**: your workspace already produces leads in several places (replies to a sequence, identified site visitors, list builds, signal-based lists), each with an approach drafted, and your reps read none of those tables. A drafted approach nobody opens is worth exactly as much as no draft. Every weekday morning this gathers what is new, routes each lead to the rep who owns the account, ranks by what the lead actually did, and sends each rep one message with at most five leads. It says a lead once, never twice, and a lead held back by the cap waits for the next morning instead of disappearing.

```
              Scheduled routine, every weekday morning
              "Send each rep this morning's new leads and the approach ready for each."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Read the roster and fix the window         ║   data_rows
║  Each rep's CRM owner email and territory, and  ║   no roster means no recap
║  the window since the last recap.               ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no roster         the roster table is still empty
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Find the lead tables, pull what is new     │   oto_project
│  New rows since the last recap, plus every      │   data_rows
│  lead that waited on an earlier morning.        │   a new lead table is picked up by itself
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no source         every lead table failed to answer
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Drop what was already said or worked       ║   data_rows on the ledger
║  The recap ledger, then any lead a rep worked   ║   hubspot_object
║  in HubSpot after its own evidence.             ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ already said      sent in a previous recap
                         ├───────────────▶  ▪ already worked    activity after the lead's own event
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Route each lead to its rep                 │   hubspot_owners
│  The CRM owner, then your territory rule, then  │
│  the manager, and never reassigned.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Tier by behavior, keep five                │   no tool is called here
│  What the lead did sets the tier, fit only      │
│  breaks ties, and tier C is cut first.          │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ over the cap      recorded as waiting, not as said
                         ▼  five leads or fewer per recipient
┌─────────────────────────────────────────────────┐
│  5 · Send one Slack DM per rep                  │   slack_find_user_by_email, data_write
│  Each lead with its evidence and first draft    │   slack_open_dm
│  line; unowned leads go to the manager.         │   slack_post_message
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Record what was said                       │   data_write
│  One ledger row per lead per recap, with its    │
│  source row, including the leads that waited.   │
└─────────────────────────────────────────────────┘

▪ terminal — it leaves this morning's recap there
```

The exits are per lead, not per run, except `no roster` and `no source`: a normal morning drops leads at every one of them and still reaches every rep who has something new. Step 4 is where the value lives. A rep who opens a message and finds five leads worth acting on comes back tomorrow; a rep who finds twenty of mixed quality stops opening it, which is why the cap is a rule and not a setting.

## Rule zero: this delivers work, it does not do it
- **It never contacts a prospect.** Every message goes to someone on your team. The drafts it surfaces were written by the processes that produced the leads, and a person launches them from the table row, never from the recap.
- **It writes only its ledger, the Slack id cache on the roster, and Slack.** The CRM and the lead tables stay read only: marking a lead handled on another process's row would break that process's own bookkeeping.
- **A rep with nothing new receives nothing.** The manager message carries the counts, so a silent morning for one rep is visible without sending that rep an empty message.
- **Every lead that survives the filters reaches a person.** A lead with no rep is named to the manager, never reduced to a count.

## Before the first run
- **A roster table**, one row per rep: name, CRM owner email, Slack member id (the run fills it once resolved), territory, and whether the rep is currently receiving recaps; plus the manager channel id. A person writes it once. Never infer the roster from CRM activity: a quiet rep would silently drop out of it.
- **A territory rule** for accounts no one owns yet: **[country, then employee band, to a rep]**.
- **A ledger table** keyed on `lead_key`, which step 6 fills.
- **Every lead table linked to a project** in the workspace, so step 1 can find it without a hardcoded list.

## 0. Read the roster and fix the window
- <tool:data_rows> — reads the roster. Empty: take the `no roster` exit and close the run as blocked in one line.
- **The window runs from the last recap to now**, as explicit timestamps: the start is the time of the last recap's run row in the ledger (or **[24 hours]** back on the first run), so a Monday covers the weekend and a skipped holiday loses nothing. A relative "last 24 hours" breaks twice a year, when a schedule set in UTC and reps on local time disagree about daylight saving, and nothing reports it.

## 1. Find the lead tables, pull what is new
- <tool:oto_project> — `op="list"`, then `op="get"` on each project: every table link comes back with its resolved `datastore_id`. **Read each table by that id, never by a name typed from memory**: several tables can share a name, and the wrong one reads empty and reports a quiet morning.
- **A table qualifies as a lead table by its shape**: under any naming, its rows carry a company or domain, a status or verdict, and a written-at timestamp. A table of counts, configuration or run summaries does not qualify. The run lists the tables it read and the ones it skipped, so a table that quietly stops qualifying is visible. The trade-off is deliberate: a new lead source is recapped without anyone editing this process, and the printed skip list is what catches a lead-shaped table with no business meaning.
- <tool:data_rows> — on each qualifying table, `filter={"_updated_at": {"gte": "<window start>"}}` with `fields` projected to what the recap needs, including the **evidence timestamp**: the time of the reply, form or visit when the row records one, otherwise the row's written-at time. Step 2 measures rep activity against it. Keep the rows whose status says a person has something to do: a drafted approach awaiting approval, a classified reply awaiting an answer, an enriched contact awaiting a first touch. A status saying the upstream process is still working on the row means it is not a lead yet.
- <tool:data_rows> — on the ledger, the rows with `cut` set whose domain has no sent row. **A lead that waited is brought back by its ledger row, not by the window**: its source row may not change again for weeks, so a filter on `_updated_at` alone would lose it the morning after it was cut. Each carries the `source_datastore_id` and `source_row_id` step 6 stored; re-read the source row with `data_rows` `id=<source_row_id>` on that datastore, one call per waiting lead, so the status and the draft are today's and not the ones recorded when it was cut. It then goes through the same actionable-status check and step 2's worked test as a new row, and joins today's candidates. A domain present both as a new row and as a waiting lead is kept once, with the newer evidence.
- **Waiting is bounded.** A lead cut on **[N]** separate mornings stops being carried and is counted as expired in the manager message; otherwise a steady supply of tier C rows would grow a backlog that no morning can clear.
- **A table that errors is reported, not skipped.** Five silent tables and one that failed look identical unless the run says which was which. If every table failed, the run takes the `no source` exit, closes as failed, and still posts the manager message.

## 2. Drop what was already said or worked
- <tool:data_rows> — reads the ledger. **A domain sent in any previous recap, to a rep or to the manager, is dropped**, unless the evidence changed materially: a company that was tier C and has since replied, booked a meeting or read a pricing page is a new lead, said again with one clause naming the previous mention. A tier that improved on firmographics alone is not material. A ledger row with `cut` set was never said, so it is not a repeat.
- <tool:hubspot_object> — `op="search"`, `object_type="companies"`, one filter `{"propertyName": "domain", "operator": "IN", "values": [up to 100 domains]}`, `properties=["domain", "hubspot_owner_id", "notes_last_updated", "hs_num_open_deals", "country", "numberofemployees"]`, `limit=100`, paged with `after`, one call per 100 domains. Normalize the domain first (lowercase, which `IN` on a text property expects, no scheme, no `www.`, no path) and never match on the display name. For person-level leads such as a reply, run the same search on `contacts` by `email`, with `properties=["email", "associatedcompanyid", "hubspot_owner_id", "notes_last_updated"]`. The owner, country and employee count read here are what step 3 routes on.
- **A lead has been worked when the `notes_last_updated` of its company or contact (last logged call, email, meeting, note or task) is later than the lead's own evidence timestamp plus [a tolerance that covers your sending tool's sync lag]**, and is then dropped even if it was never recapped. Telling a rep about a lead they worked yesterday is the fastest way to teach them to stop reading.
- **Activity synced by your sending tool moves this date too.** A reply to a sequence lands in HubSpot as a logged email, so a rule of "any activity inside the window" drops exactly the tier A leads, before any rep has touched them. Measured against the lead's own evidence time, the logged reply does not count as work done on itself.
- **Do not use `hs_lastmodifieddate` either**: any property write, an enrichment sync or a workflow, moves it, and leads nobody touched would vanish from the recap.

## 3. Route each lead to its rep
- <tool:hubspot_owners> — once per run, turns every owner id read in step 2 into an email, and the email finds the roster row.

Then, in this order, stopping at the first that resolves:
1. **The CRM owner of the company**, when that owner's email is on the roster and marked as receiving recaps.
2. **Your territory rule**, for a company with no owner, matched on `country` and then on the employee band from `numberofemployees`. A company the CRM does not hold at all takes both from the lead row.
3. **The manager**, for everything else, including an owner the roster marks as not receiving recaps or does not list at all.

Never round-robin a lead that already has an owner, and never write ownership back to the CRM. A lead routed to the manager is **named** in the manager message, as a lead the manager hands out: a steady stream of them means the territory rule or the roster needs updating, not that routing failed.

## 4. Tier by behavior, keep five
The tier is set by what the lead did, not by what the company looks like. An ideal-profile company that did nothing is not a hot lead, and the ranking exists to stop presenting it as one.

| Tier | What earns it | Examples |
|---|---|---|
| A | A person acted toward you inside the window | a positive or ambiguous reply to a sequence, a form submission on your site, a demo or webinar registration |
| B | A named person at a company that showed real interest | an identified company that read a pricing, security or product page for more than a glance, with a person resolved and a draft ready; someone engaging with your company page, with a draft ready |
| C | A company that matches your profile, with no behavior behind it | a row from a list build, a signal row with a job posting or a stack change and nothing else |

- Within a tier, order by the recency of the evidence, then by fit against **[your ideal customer profile]**.
- **At most five per recipient**, and the manager's unowned leads are ranked and capped the same way. On a morning with more, **tier C is cut entirely before any tier B is cut**. A sixth lead is never appended; it waits, recorded as cut, and the manager message says how many waited.
- For each surviving lead, one approach line: the company, the person and their title, one clause of evidence in the lead's own terms ("replied asking about pricing", "read the security page twice this week"), and the **first line of the draft already written, quoted exactly, never restated**. Each draft keeps the voice the producing process wrote it in; the recap never rewrites them into one house voice.

## 5. Send one Slack DM per rep
- <tool:slack_find_user_by_email> — only when the roster row has no member id yet. An owner email that resolves to no Slack member is reported in the manager message, and that rep's leads go to the manager for this morning rather than nowhere.
- <tool:data_write> — writes the resolved member id back to the roster row, so tomorrow skips the lookup.
- <tool:slack_open_dm> — `user` set to the member id returns `channel.id`. **A DM is posted to that channel id, not to the user id.**
- <tool:slack_post_message> — to the DM channel: one line with the count and the tiers, one block per lead in rank order, then one sentence saying where to act, which is the table row, not this message. Then one reply per lead under the message's `ts`, carrying the full draft, the rest of the evidence and the link to the row. Five full drafts will not fit one readable message; the first line in the message and the rest in the thread is what does.
- **A DM can be refused by workspace policy**, typically to someone who never interacted with the app. That is a finding for the manager message, and the refused rep's leads are named there too, not a crash.
- <tool:slack_post_message> — to the manager channel, once. First the counts: leads sent per rep, leads that waited, leads that expired, leads dropped as already said or already worked, emails that did not resolve, DMs refused, and every table that failed. Then **the leads routed to the manager, by name**, in rank order and capped at five: company, tier, evidence clause, first draft line and the link to the row, with the full drafts in the thread under its `ts`, as a rep would receive them. It posts on a failed run too: it is the only place a quiet morning and a broken run can be told apart.

## 6. Record what was said
- <tool:data_write> — one batch with `key="lead_key"`, where `lead_key` = normalized domain + recap date: `source_datastore_id` and `source_row_id` (what lets step 1 re-read a lead that waited), the tier, the evidence clause, the recipient (the rep, or `manager` for an unowned lead), the Slack `ts` of the message, and `cut` set when the lead waited instead of being sent.
- **Manager-routed leads are recorded like any other**, with the recipient set to `manager`, so "said once" holds for them too and tomorrow does not name the same unowned lead again.
- **One run row per recap**, keyed `run|<recap date>` with the run's start time, written even on a morning with no lead at all: it is where step 0 reads the next window's start, and a morning with nothing to say must still move it.
- The recap date in the key keeps one row per appearance, so a lead cut on Monday and sent on Tuesday leaves both, and step 2 asks "was it ever sent" rather than "was it ever seen". Counting a domain's cut rows is what tells step 1 how many mornings it has waited.

## Output
One Slack DM per rep with something new, at most five ranked leads each with the approach prepared, and one message in the manager channel naming the unowned leads and carrying the counts. The run reports, per rep, the leads sent and the leads that waited, plus the leads routed to the manager, the leads that expired, the leads dropped as already said or already worked, the tables read and skipped, and any table, email or DM that failed.