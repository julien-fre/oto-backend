# Turn LinkedIn company page visitors into outreach

**When to use it**: people follow your company page, react to and comment on your posts, and nobody on the team works that list. LinkedIn never tells page admins who viewed the page, only aggregate demographics, so this starts from the people who left a trace instead: new followers, post engagers, profile viewers of your reps, and companies that clicked through to your site. Every weekday it keeps the ones whose attention cost them something and stages a first email that opens on what they touched, for a person to review and launch.

```
              Scheduled routine, every weekday morning
              "Sweep who engaged with our LinkedIn page and posts since yesterday."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Check which sources answer                 ║   phantombuster_get_agent
║  Every phantom configured and last finished,    ║   snitcher_workspace
║  and the visitor site reachable.                ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no source         nothing configured or answering
                         ▼  at least one source answers
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  2 · Scrape who engaged          │  │  3 · Find referred companies     │   snitcher_session
│  PhantomBuster: followers,       │  │  Sessions whose referrer is one  │   referrer filter, paged to total
│  engagers, profile viewers.      │  │  of the network's two domains.   │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Merge on the profile, exclude              │   lemlist_unsubscribe
│  One row per person, then staff, competitors,   │   merged on the normalized profile URL
│  recruiters and opt-outs out.                   │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ excluded          staff, competitor, recruiter, opt-out
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Give each person a lane                    ║   hubspot_object
║  Customers and open deals go to their owner in  ║   the stage id decides, never the label
║  the recap, whatever the trace.                 ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ customer          named to the account owner
                         ├───────────────▶  ▪ open deal         named to the deal owner
                         ▼  no record, or a lost deal
╔═════════════════════════════════════════════════╗
║  6 · Ask whether the attention is real          ║   no call, the traces came with 2 and 3
║  Two traces, or one that cost something to      ║
║  leave, like a public comment.                  ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ thin signal       a bare follow or a lone reaction
                         ▼  the attention cost something
┌─────────────────────────────────────────────────┐
│  7 · Resolve the person, one email              │   apollo_match_person
│  Title and company confirmed, one work email,   │   fullenrich_enrich_linkedin
│  no duplicate in a campaign.                    │   lemlist_lead
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ out of profile    no persona or segment fits
                         ├───────────────▶  ▪ no email          nothing resolvable after both
                         ├───────────────▶  ▪ in sequence       already in a campaign
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Draft from what they touched               │   lemlist_campaign
│  The opener names the post they touched; the    │   lemlist_create_lead
│  first lead is proven held before the rest.     │   hubspot_object
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ not staged        review hold unconfirmed, or counters moved
                         ▼
┌─────────────────────────────────────────────────┐
│  9 · Record every person, post one recap        │   data_write
│  One row per profile, and a recap naming only   │   slack_post_message
│  what is new since the last.                    │
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

The steps follow one person through the run. Steps 2 and 3 run in parallel and merge at step 4, because the same person can follow the page and comment on a post in the same week and must not become two rows. Every free check (the merge, the exclusions, the CRM lane and the attention gate) runs before the first paid lookup at step 7, and the lane comes before the gate so an account owner hears about a customer's lone reaction too. The exits are per person, and the recap gives their counts, not their names.

## Before the first run
- **A sources table**, one row per source: the phantom id, what it runs against (your company page, your recent posts, a rep's profile), its per-launch cap, and whether it's enabled. A follower-collector phantom needs admin access to the page, and a profile-viewers phantom needs a rep seat that exposes viewers; a source you don't have yet stays in the table as disabled, so every recap says it was not read.
- **A people table** upserted on `profile_url`, which also holds the follower baseline the next run diffs against.
- **A dedicated outreach campaign** with auto-review off, so new leads wait for review, and **a person's confirmation of that recorded in the sources table**, with who and when: the run can't read that setting, and on a campaign with no leads yet nothing else proves it. Plus **the recap channel**, with the bot a member.
- **Your never-touch rules** (**[your own company and its alumni, your competitors, your standing exclusions]**), **your buyer personas with the titles that map to each**, and **your segment**.

## 1. Check which sources answer
- <tool:phantombuster_get_agent> for every enabled phantom in the sources table: its configuration and its last status. A phantom that has never finished, or whose configuration no longer points at your page or posts, is reported as not answering rather than silently skipped.
- <tool:snitcher_workspace> `op="list"` for your site's workspace uuid. **Read it every run, never hardcode it**, so the run always reads the workspace currently tracking your site.
- If no source answers, record it and post one line: nothing is spent on a run that has nothing to read.

## 2. Scrape who engaged
- <tool:phantombuster_launch_agent> with the phantom's `agent_id` and a `config` override carrying what it runs against and the cap from the sources table. For the engagers phantoms, that is your page's posts from the last **[few weeks]**.
- **PhantomBuster is asynchronous.** The launch returns a container id, not results. Poll <tool:phantombuster_get_container> until the status is finished, then read <tool:phantombuster_container_results> with that `container_id`. Reading before the container finishes returns an empty or partial result, which looks exactly like a day with no new followers.
- **A phantom runs on a logged-in session, and the session expires.** An expired session makes a phantom finish successfully with zero rows. **Zero rows from a phantom that returned rows on its previous run is a failed source**, reported as one, never a quiet day; so is a count far below the previous run's.
- **The follower list is cumulative, so the signal is the difference.** A follower collector returns every follower, not the new ones. Diff against the baseline in the people table and act only on the difference. **The first run records the baseline and stages nothing**: otherwise every existing follower lands in a sequence on day one.
- **Scraping carries account risk**, and volume is what gets a session restricted. Run each phantom at most once a day, keep the caps from the sources table, and never raise a cap to catch up after a failed day.

## 3. Find referred companies
- <tool:snitcher_session> workspace-wide, with `date_from` and `date_to` a couple of days wider than the real window, `referrer="linkedin"`, then a second pass with `referrer="lnkd.in"`, the network's link shortener, since a click through a shortened link can carry that referrer instead. Cut to the real window in memory on the session timestamp rather than trusting the date bounds to the hour.
- **Page through each pass** with `page` and `size` until the sessions collected match the `total` the response carries. A widened window on a busy site runs past one page, and a cut-off read looks like fewer referred companies, not like an error.
- **The company on a session is a reverse-IP guess; the visit is real.** A referred company never becomes a row of its own. It joins the merge as evidence on a company, and it counts as a second trace only for a person whose company matches it.
- Read <tool:snitcher_organisation> `op="get"` only when you need a company's domain or pages visited to make that match. **Never call `snitcher_contact` with `op="reveal_email"`**: it spends a credit and can't be undone, and this process finds its email elsewhere.

## 4. Merge on the profile, exclude
- **Merge on the profile URL, normalized**: lowercase, no query string, no trailing slash, only the `/in/<slug>` part. A person who followed and commented in the same window is one row with two traces, and the two traces are what step 6 counts. When one source returns an id-style profile URL instead of the readable slug, merge on name plus company as a fallback and flag the row `id_url`, rather than letting one person become two.
- **The never-touch list, applied here, before anything is spent**: anyone whose current company is yours (staff and alumni), anyone at a competitor, recruiters, students and job seekers (their attention is on your careers page, not your product), and anyone who already asked not to be contacted.
- <tool:lemlist_unsubscribe> `op="var_export"` once per outreach team you send from, then match in memory: the values list holds LinkedIn URLs as well as emails, which is what makes an opt-out checkable before an email is known. The email-and-domain list is checked again at step 7, once there is an email.
- Excluded rows are written with their reason and go no further.

## 5. Give each person a lane
- **The lane costs no credits, so it runs on every row the exclusions let through**, before the attention gate: an account owner should hear that their customer reacted to a post even when that reaction alone would never earn a draft. A row with no company on its profile has no lane to find and goes straight to step 6.
- <tool:hubspot_object> `op="search"`, `object_type="companies"`, on the company's name (or its company page URL, if your portal stores one), **once per distinct company in the run**, not once per person, then `op="get"` on a match with `associations=["deals"]`, which returns the deal ids inline, and `op="get"` on each deal for `dealstage` and `pipeline`. **Read stage ids, never labels**: labels get renamed, ids don't.
- A live deal in your customer pipeline makes the row `customer`; any open deal makes it `open deal`. Both stop here with a line in the recap naming the owner on the record and the trace, however thin: the account owner should know their customer is engaging with your posts, and a prospect in a live deal should not receive a competing first touch from a sequence.
- **A name match is a candidate, not a confirmation.** Step 7 re-runs this check on the resolved domain before anything is staged, so a customer whose name didn't match here is still caught before any lead exists.
- No record, or a lost deal, continues. The row says it was previously lost, so nobody re-pitches it blind.

## 6. Ask whether the attention is real
No call here; every trace came with steps 2 and 3. **Two traces, or one that cost something to leave.**

| Keep | Because |
|---|---|
| A comment on one of your posts | public, attributable, usually about a specific claim |
| A reaction plus any second trace | two independent moves in one window |
| A follow plus a referred session from their company | they followed, then went to look |
| A profile view of a rep plus any other trace | they looked the rep up on purpose |

**Drop as thin signal**: a bare follow, a lone reaction, and any trace from a profile with no title or no company. Those rows are recorded with the verdict and never drafted. A list nobody trusts gets ignored, and the fastest way to lose a rep's attention is a morning list of bare follows.

## 7. Resolve the person, one email
- <tool:apollo_match_person> with `linkedin_url`, one call per person who reached this step, never before the gates. **It costs a credit even when nothing matches**, and a response carrying `person._stub: true` is a failure, not data. Confirm the title and the current company from Apollo, not from the scraped headline, which is often a slogan.
- **A row flagged `id_url` at step 4 never goes in with that URL**: it can't match, and the credit is spent anyway. Call it with `first_name`, `last_name` and `org_name` (or `domain`, when step 3 or HubSpot gave one), and take the readable slug from the `linkedin_url` Apollo returns.
- **Check the fit now**: a title that maps to none of **[your buyer personas]** at a company outside **[your segment]** stops as `out of profile`.
- <tool:fullenrich_enrich_linkedin> for everyone Apollo resolved without an email, **in one job of up to 100 contacts**, each with its `linkedin_slug` (the slug, not the URL) and the company `domain`, and `enrich_fields=["contact.work_emails"]` only: it bills per result, and phones cost many times more than a work email. A flagged row that still has no readable slug goes in on name plus `domain`, and one with neither stays out of the job. Poll <tool:fullenrich_result> after about 30 seconds, then every 20 to 30 seconds until it's done. Nothing from either is `no email`: the row stays in the table for someone to work by hand.
- Re-run the step 5 lane on the resolved company `domain`, and check the email and its domain against `lemlist_unsubscribe` `op="export"`.
- <tool:lemlist_lead> `op="get"` with `email`, in each outreach team you send from. **Deduplicate by email, never by name.** Someone already in a campaign is `in sequence`, and the row names the campaign.

## 8. Draft from what they touched
- **One email, in the persona their title implies, opening on the specific thing they touched**: the post they commented on, quoted in one clause; the post they reacted to, by its subject; the fact that they follow you, only when a second trace backs it.
- **Never write "I saw you viewed our page."** The run doesn't know that, because the platform doesn't expose it, and a sentence claiming it reads as surveillance. Never mention the site visit either. Say only what is true and public: they commented, they reacted, they followed.
- Claims come from your positioning, numbers only from your published figures exactly as they stand, and a customer is named only if they're on your citable list.
- **With auto-review on, creating a lead is a send, and no read returns that setting.** <tool:lemlist_campaign> `op="reports"` with `campaign_ids=["<campaign id>"]` returns counters only: `totalCount`, `reviewedCount`, `inSequenceLeadCount` and `emailsSent`. A lead held for review raises `totalCount` alone; a lead auto-review launched raises the others with it.
- **On a campaign with no leads yet, every counter is 0 whether review is on or off, so the read proves nothing.** Such a campaign is staged into only when the sources table records a person's confirmation that review-before-send is on. Without one, nothing is staged, the drafts are recorded as `not staged`, and the recap says why.
- **Create the first lead of the run alone, then read `reports` again.** `totalCount` up by one with `reviewedCount`, `inSequenceLeadCount` and `emailsSent` unchanged means it is held, and the rest follow. If any of those three moved, stop: the remaining drafts are `not staged` and the recap opens on it. A campaign someone switched to auto-review then costs one lead, not the day's list; and a person launching from the queue in the same seconds only causes a false stop, the safe direction.
- <tool:lemlist_create_lead> in that campaign with the email, name, company, title, `linkedin_url`, the opener in `icebreaker` and the rest in `custom_variables`, **all in the create call**, so the lead never exists without its opener, plus `deduplicate=true` as a second guard. The lead waits for review. This process never calls `lemlist_launch_lead` or `lemlist_campaign_auto_review`.
- <tool:hubspot_object> `op="add_note"`, `object_type="companies"`, on the company record when one exists: the trace, the date and the staged lead. A note, not a property change, so a person can delete it; and never a new company record.

## 9. Record every person, post one recap
- <tool:data_write> — one row per person, upserted on the normalized `profile_url`: every trace with its date, the verdict, the company and its lane, the resolved title and email, the draft, the campaign and lead ids, and `recap_ts`. **A person reviews, edits and launches each draft in the outreach tool's review queue**; the row keeps the evidence behind it, and the recap only points to it.
- <tool:slack_post_message> once. **Say a person once**: someone already named in an earlier recap is named again only if a new trace arrived since. The recap opens with the counts, then the new drafts waiting for review, then the customers and open deals whose people engaged, with their owners, then one line of skipped counts by reason, then a sources line saying which sources answered and which didn't.
- **A day with nothing new still posts its header and its sources line.** A quiet day and a broken run must not look alike, and a day where every phantom returned zero after returning rows the day before is almost always an expired session, so it says so.

## Output
One Slack recap per weekday: counts per source and per verdict, each new draft waiting for review with the person, the trace it answers and the opener, the customers and open deals whose people engaged with their owners named, the skipped counts by reason, any stop on the review-hold check, and which sources answered. One row per person in the people table with every trace and its verdict, a lead held for review in the outreach campaign for each draft, and a note on the CRM company where one exists.