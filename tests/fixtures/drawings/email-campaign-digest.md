# Post weekly Brevo newsletter results to Slack

**When to use it**: you send marketing campaigns every week and nobody counts what they did. Open rates get quoted against the wrong denominator, a deliverability problem is found by recipients before anyone on the team, and someone who unsubscribed from the newsletter gets a cold sequence from the sales tool days later. This reads last week's campaigns every Monday, gives each one a verdict, keeps one row per campaign per week, and posts a single digest with the deltas. It reads and never writes to any sending tool.

```
              Scheduled routine, every Monday morning
              "Digest last week's email campaigns with deliverability and week-over-week deltas."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull last week's sent campaigns            │   data_rows
│  Rows an earlier run left open first, then      │   brevo_campaign
│  every campaign sent inside the ISO week.       │   status=sent, statistics=globalStats
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ still sending     in process or overdue, counted once done
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Compute the rates and the verdict          ║   no call, the counts came with step 1
║  Rates against delivered, then the worst        ║   under 24 hours old stays unsettled
║  threshold decides the verdict.                 ║
╚════════════════════════╤════════════════════════╝
                         ▼  rates computed once
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  3 · Read what people clicked    │  │  4 · Check the unsubscribe drift │   brevo_contact
│  Brevo link statistics, top      │  │  Brevo opt-outs the CRM and the  │   hubspot_object
│  three links per campaign.       │  │  outreach tool don't carry.      │   lemlist_unsubscribe
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Write the week, read the last              │   data_write
│  One row per campaign per ISO week, then last   │   data_rows
│  week's rows for the deltas.                    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Post one digest                            │   slack_post_message
│  Rates, verdicts and deltas in one message, and │   detail in the thread
│  a quiet week still posts.                      │
└─────────────────────────────────────────────────┘

▪ terminal — the campaign is left out of this week's counts; the digest still names it
```

Steps 3 and 4 only read, so they run in parallel once the rates are computed. The one exit is per campaign: a campaign still going out on Monday is left out of this week's counts and picked up by a later run once it has finished, never counted half-done.

## Before the first run
- **A campaign weeks table** with one row per campaign per ISO week: `campaign_week` (the upsert key: the campaign id plus the ISO week of its send, or of its scheduled send while it is still going out), `campaign_id`, `name`, `type`, `scheduled_at`, `sent_at`, the raw counts, the five rates (open, click, unsubscribe, complaint, bounce), `verdict`, `unsettled`, `top_links`, `state` (`counted` or `pending`).
- **A campaign naming convention** that carries the type (**[newsletter, product announcement, event, nurture]**, for example as a prefix). Deltas are computed per type, so a name the convention can't parse lands under `other` and says so in the digest.
- **The channel** your marketing owner reads, with the bot already a member.
- **Your deliverability thresholds**, or the starting ones in step 2 until your own history replaces them.

## 1. Pull last week's sent campaigns
- The window is the ISO week that ended on the Sunday before the run, written as two explicit dates in your reporting timezone, never as "last week". A relative window drifts the moment a run is late or replayed.
- **Start with what an earlier run left open.** <tool:data_rows> for the rows whose `unsettled` is true or whose `state` is `pending`, whatever week they belong to. Re-pull each one alone with <tool:brevo_campaign> `op="list"`, `campaign_id=<id>`, `statistics="globalStats"`. A campaign that has now finished sending, or is now more than 24 hours past its send, goes through step 2 like any other and is written back in step 5 **under its original `campaign_week` key**, never re-keyed on this run's week. The digest names it as settled from an earlier week. Without this pass, a campaign sent on a Sunday would never get a verdict: the next window starts after it. A campaign still in process on the re-pull simply stays `pending`.
- Then <tool:brevo_campaign> `op="list"`, `status="sent"`, `statistics="globalStats"`, `limit=50`, then `offset` in steps of 50 **until a page comes back shorter than the limit**. A week with more campaigns than one page returns a plausible partial answer, and a partial answer reads like a quiet week.
- **Cut on the send date, not the creation date.** A campaign drafted a month ago and sent on Tuesday belongs to this week. Keep only campaigns whose `sentDate` falls inside the window.
- **One row per campaign id, ever.** Before keying a sent campaign, look its id up in the table: an id that already holds a row (a pending one that finished, a send that crossed midnight on a Sunday) is settled through that row and never given a second key. This is what keeps a campaign from being counted twice, in two different weeks.
- **The same call leaves the HTML body out of its responses**, on purpose, for volume. A campaign that comes back without its content is normal; never report it as empty.
- From each campaign keep the id, the name, the type parsed from the naming convention, `scheduledAt`, `sentDate` and the counts: `sent`, `delivered`, `uniqueViews`, `uniqueClicks`, `unsubscriptions`, `hardBounces`, `softBounces`, `complaints`.
- **Still sending means in process, or queued past its time.** Run the same list with `status="inProcess"`, and with `status="queued"` keeping only campaigns whose `scheduledAt` is before the run. Brevo also files a campaign booked for Thursday as `queued`: that is a future send, not a stuck one, and it is left out entirely rather than reported as sending. Write each still-sending campaign as a `pending` row keyed on its id plus the ISO week of its `scheduledAt`, and name it in the digest. When a later run finds it finished, **that same row flips to `counted`**, so no pending row is left behind.

## 2. Compute the rates and the verdict
No call here; everything came back with step 1.
- **Open, click, unsubscribe and complaint rates go against `delivered`, never against `sent`.** An open rate computed against sent flatters every campaign by exactly its bounce rate, silently, which is how a deliverability problem hides inside a rising open rate.
- **The bounce rate is the one exception**: hard plus soft bounces against `sent`, because a bounce is precisely a message that was not delivered.
- Read the open rate with care: privacy-protecting mail clients register opens nobody made. The click rate is the engagement number the deltas should lean on.
- **The verdict is the worst of three thresholds, not their average.** A campaign healthy on bounces and degraded on complaints is degraded, because the complaint rate is the one that costs the sending domain.

| Verdict | Bounce rate | Unsubscribe rate | Complaint rate |
|---|---|---|---|
| healthy | under **[2%]** | under **[0.5%]** | under **[0.1%]** |
| watch | **[2 to 5%]** | **[0.5 to 1%]** | **[0.1 to 0.3%]** |
| degraded | above **[5%]** | above **[1%]** | above **[0.3%]** |

- **These are starting thresholds, stated rather than measured.** Until **[eight]** weeks of rows exist, the digest carries one line saying so; after that, a person replaces them with your own distribution.
- **A campaign sent less than 24 hours before the run is `unsettled`**: its counts are recorded, it gets no verdict, and the digest says it settles next Monday, when step 1 reads it back. Opens and clicks keep arriving for days, and a verdict given on the first morning is a verdict about timing.
- **Compute each of the five rates once.** Step 5 stores exactly those values and the digest reads them from the same place, so the table and the message can never disagree.

## 3. Read what people clicked
- <tool:brevo_campaign> `op="list"`, `campaign_id=<id>`, `statistics="linksStats"`, only for campaigns with `uniqueClicks` above zero. **One statistics shape per call**, with the campaign id as the join: never assume a single response carries both the counts and the links.
- Keep the three links with the most clicks. This is the part a marketing owner acts on: it says whether the call to action earned the clicks or the traffic went to the footer. **A campaign whose most-clicked link is the unsubscribe link is a finding with its own line.**

## 4. Check the unsubscribe drift
Your unsubscribe promise has to hold on every surface that can email the person: the marketing platform, the CRM's opt-out, and the outreach tool's do-not-contact lists. This step counts the gap and writes nothing.
- <tool:brevo_contact> `op="list"`, `modified_since=<window start>`, `limit=1000`, paged with `offset`, keeping contacts whose `emailBlacklisted` is true. `modified_since` catches any change to a contact, so **the blacklist flag is the filter, the date only narrows the read**. A list-level unsubscribe is narrower than a global opt-out and is not counted as drift.
- <tool:hubspot_object> `op="search"`, `object_type="contacts"`, `filters=[{"propertyName": "email", "operator": "IN", "values": [...]}]`, with `properties` set to `email` and **[your marketing opt-out property]**, in batches of up to 100 addresses (the page cap, so one batch is one page), lowercased first so the match doesn't depend on how each system stored the case. An address that carries no opt-out is drift. An address with no CRM contact at all is counted separately: the CRM can't email someone it doesn't hold.
- <tool:lemlist_unsubscribe> once per outreach workspace you send from, one export per list, then match in memory: `op="export"` for the email and domain list, `op="var_export"` for the values list, and `op="contact_export"` for the do-not-contact flag carried by a contact, matched on that contact's email. **The three lists don't write to each other**, so an address on one is not necessarily on the others, and one `get` per address scales badly on a busy week. An address on any of the three, or covered by a domain-level entry, counts as carried.
- The reason this is counted at all: someone who unsubscribes from a newsletter and receives a cold sequence days later has been told the unsubscribe meant nothing, and this count is the only place that shows it before the recipient does.
- **Read only.** Syncing an opt-out is a person's call or another process's job. Name at most **five** drifted addresses, in the thread, never in the main message.

## 5. Write the week, read the last
- <tool:data_write> — one row per campaign per ISO week, upserted on `campaign_week`, so replaying the same Monday updates rows rather than duplicating them. Store the counts, the five rates as computed, the verdict, the unsettled flag and the top links. A row settled from an earlier week keeps its original key: its `unsettled` flag drops or its `state` flips to `counted`.
- <tool:data_rows> — the previous ISO week's rows, read **after** the write, so a campaign settled this morning feeds the comparison with its final figures. **Compute the deltas per campaign type, not per campaign**: campaigns don't repeat, types do. A type with no campaign last week gets no delta rather than an infinite one.

## 6. Post one digest
- <tool:slack_post_message> once. The message opens with the week's dates and the number of campaigns, then one line per campaign (name, delivered, open and click rates, unsubscribes, verdict), then the campaigns settled from an earlier week with their final verdict, then the deltas by type, then the drift count only when it isn't zero, then the starting-thresholds line while it still applies.
- **The detail goes in the thread**: post the per-campaign top links and the drifted addresses with the returned `ts` as `thread_ts`.
- **A quiet week still posts one line** saying no campaign went out. A silent Monday and a broken run must not look alike.
- The bot has to be in the channel, or the post fails with `not_in_channel`. A message over roughly 4,000 characters is split into threaded parts rather than truncated: read `split_into` on the response before concluding anything was lost, because a duplicate can be deleted but not edited.

## What this never does
- Never sends, never schedules, never edits a campaign. `brevo_campaign` `op="test"` **sends for real**, one enum value away from the call this process makes, and it is never called here; neither is `brevo_send_email`.
- Never writes to the CRM or the outreach tool, even to fix the drift it finds.
- Never rounds a rate into a better story, and never softens a `degraded` verdict.

## Output
One Slack digest per Monday: the week, each campaign with its rates and verdict (or `unsettled`), the campaigns settled from an earlier week with their final verdict, the deltas by campaign type, the campaigns still sending, the unsubscribe drift count when it isn't zero, and the starting-thresholds note while it applies; top links and drifted addresses in its thread. One row per campaign per ISO week in the campaign weeks table, which is what next Monday's deltas and settling pass read.