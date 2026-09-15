# Draft a Brevo email campaign for each shipped feature

**When to use it**: your team ships and the market isn't told, or it's told by a campaign assembled by hand that can't see who already got several emails this month or who is in the middle of a sales conversation. This takes one published release, writes the announcement from your own product pages for the persona the feature serves, cuts the audience by suppression, a frequency ceiling and open deals, and leaves an unsent draft in your email tool for a person to read and send. Run it on demand when you name the release, or as a weekly sweep of the changelog.

```
              Natural language input in Claude
              "Draft the email campaign for the feature we published this week."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Find the release worth announcing          │   productlane_changelogs
│  The release you named, or the newest published │   published only, never broadcast
│  feature not yet campaigned.                    │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing shipped   only fixes since the last campaign
                         ├───────────────▶  ▪ already staged    the release already has a row
                         ▼  a new capability, published
┌─────────────────────────────────────────────────┐
│  2 · Ground the copy in your own pages          │   productlane_docs
│  What the feature does, who it serves, and the  │   oto_doc
│  only numbers the copy may use.                 │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Resolve the audience, then cut it          ║   brevo_list
║  The persona's list, minus suppression, the     ║   hubspot_object
║  frequency ceiling and open deals.              ║   data_rows
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ audience empty    everyone on the list was excluded
                         ▼  someone is left to write to
┌─────────────────────────────────────────────────┐
│  4 · Write the campaign                         │   no roadmap, no competitor, no price
│  Three subject lines, a preheader, four short   │
│  paragraphs, one call to action.                │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Stage it as an unsent draft                │   brevo_account
│  A draft carrying the segment list, both        │   brevo_campaign
│  exclusion lists and a verified sender.         │   no send, no test
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Record it and tell the team                │   data_write
│  A campaign row, one ledger row per recipient,  │   slack_post_message
│  and one short staging notice.                  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · A human reads it and sends it              │   the one human step
│  Picks one subject, sends a test to themselves, │
│  then schedules it in Brevo.                    │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there for that release or persona; the notice still says why
```

The steps follow a single campaign. Steps 1 and 2 run once per release; steps 3 to 6 run once per persona, because a feature that serves two personas gets two campaigns with two bodies, never one body sent twice. The exit at step 3 is per persona, so one run can stage one campaign and discard another, and the notice says both. The weekly sweep and the on-demand run differ only at step 1.

## Before the first run
- **One segment list per persona** you announce to (**[your personas]**) and **one suppression list**, both in Brevo, with their ids in a config table. No list ids means no audience: the run stops as blocked rather than invent one. **An enriched or scraped prospect never enters these lists**; people who never opted in belong to a one-to-one outreach channel with its own rules.
- **A Brevo folder for hold lists** (`brevo_list` `op="folders"` returns its id), because creating a list requires one.
- **A release campaigns table** keyed on `campaign_key` (the release id plus the persona), with a `status` of `staged`, `sent_by_person` or `discarded`, and **a send ledger** keyed on `send_key` (the email plus the campaign id).
- **Your feature-to-persona map**: which persona each module or capability answers (**[module A → persona 1, module B → persona 2]**).
- **Your own pages**, in your knowledge base: product overview, pricing page, persona pains, positioning claims, published figures and the list of customers you may name.
- **A verified sender** in Brevo and **the marketing channel** the notice goes to, with the bot a member.

## 1. Find the release worth announcing
- When the operator named a release, take it and go straight to the replay guard. Otherwise, <tool:productlane_changelogs> `op="search"`, `published=true`, `limit=50`, paging with the returned `cursor` rather than assuming one page covers the window, then `op="get"` with `changelog_id` for a candidate's full body.
- <tool:data_rows> on the release campaigns table: keep only entries published after the newest row. **The replay guard is the table, keyed on the release.** A release already there as `staged` or `sent_by_person` is skipped and named in the notice, because a second draft of a campaign someone already sent is worse than no draft. A `discarded` row is skipped too, unless the operator named that release explicitly, which is how a person asks for a second attempt.
- **What counts as worth announcing**: an entry that names a new module or capability. A bug fix, a copy change or a dependency bump does not, and a sweep that finds only those says the week was quiet rather than manufacturing a campaign.
- **An unpublished entry is not a release.** Publishing and broadcasting are separate gestures in the changelog tool, and broadcasting never touches `published`, so the two states drift. A campaign written from an unpublished entry links readers to a page they can't see: filter on `published=true` and confirm the entry's link resolves before any copy is written.
- **Never call `op="broadcast"`, in either mode.** It emails every subscribed contact and posts to the configured Slack channels, with no undo. It is dry-run by default, which makes one wrong `dry_run=false` the entire failure, and it would announce the release from the wrong system while duplicating the campaign this run is staging.

## 2. Ground the copy in your own pages
- <tool:productlane_docs> `op="articles"`, `published=true`, `title_contains=<the feature>`: the help article that becomes the call to action when the changelog entry is too thin to land on. **Check its `visibility`**: `public` is linkable, `unlisted` works as a direct link, and an `internal` or `agent` article is one a customer can't open.
- <tool:oto_doc> `op="search"` on the feature's name, then read, in this order: the product overview for what the feature is, the pricing page for which plans carry it, the persona notes for the pains each persona is addressed with, the positioning claims, the published figures and the citable customers. **Your knowledge base comes before the web** for any fact about your own product.
- **Decide the persona from the feature, not from the audience size.** The feature-to-persona map names it. A big list is not a reason to send a feature to a persona it doesn't serve.

## 3. Resolve the audience, then cut it
- <tool:data_rows> on the config table for the persona's segment list id and the suppression list id.
- <tool:brevo_list> `op="get"` on the segment list for its own contact count, then `op="contacts"` with `limit=500` (the per-call maximum) and `offset`, **until what you hold matches that count**. Treat a short page as the end of the list only when the counts agree.
- Then cut, **counting each cut separately**, because the notice names them:
  1. **Suppression is not subtracted by hand.** It goes into `exclusionListIds` at step 5, so Brevo applies it at send time. An audience subtracted by hand is correct at staging and wrong by the time a person presses send. Still count how many of the segment are on it: a suppressed address inside a segment list means the list was built wrongly, and that is worth a line.
  2. **The frequency ceiling**, from the send ledger read with <tool:data_rows>: anyone past **[one marketing email in the last seven days, or three this calendar month]**, counted per contact across every campaign type, and ignoring ledger rows whose campaign ended `discarded`. Brevo can't apply this one, because it doesn't know about campaigns sent from anywhere else.
  3. **Open deals.** <tool:hubspot_object> `op="search"`, `object_type="deals"`, `filters=[{"propertyName": "dealstage", "operator": "IN", "values": [<your open stage ids>]}]`, `limit=100`, paging with `after`. Read the stage ids from your pipeline, never the labels, which get renamed. Then `op="get"` on each deal with `associations=["companies"]`, which returns the company ids inline instead of a separate associations call per deal, and `op="get"` on those companies for `domain`. Exclude every audience contact whose email domain matches one of those company domains, both lowercased and the company's stripped of `www.`, unless the deal itself is about this feature. A sales conversation and a marketing email about the same product in the same inbox the same week is the failure this cut exists for.
- **Cuts 2 and 3 travel as a hold list.** <tool:brevo_list> `op="create"` with the future campaign's name and your hold folder's `folder_id`, then `op="add"` with the excluded `emails`, **at most 150 per call and one identifier type per call**. Read `contacts.failure` on every response: an unknown contact fails inside a call that still returns successfully.
- If nobody is left, write the campaign row as `discarded` with the reason, say it in the notice, and move to the next persona.

## 4. Write the campaign
One body, in the segment's language, addressed to one persona.
- **The opening states what shipped**, in the feature's own words from your product overview. No warm-up, no compliment.
- **The second paragraph is the persona's pain**, taken from your persona notes; **the third is what the feature now does about it**.
- **The call to action is the changelog entry or the help article**, never a demo booking: this is an announcement, not a sequence.
- **Three subject lines**, each under **[55]** characters, for the person to choose from. **A preheader is written**, never left to the first line of the body.
- **What the copy may not do**: promise anything not already shipped, name a competitor, restate a plan's price, quote a number that isn't in your published figures exactly as it stands there, or name a customer not marked citable. The unsubscribe link stays in the body, and Brevo's own unsubscribe handling stays on.

## 5. Stage it as an unsent draft
- <tool:brevo_account> with `senders=true` lists the verified senders. **A campaign created with an unverified sender looks like a success at creation and fails the moment a person tries to release it.** If none is verified, still create the draft (the writing is the slow part), record the gap, and say in the notice that it can't go out until a sender is verified.
- <tool:brevo_campaign> `op="create"` with `name="<release> · <persona> · <date>"`, `sender={"email": ..., "name": ...}`, `subject` (the first proposed line), `preview_text`, `html_content`, `reply_to`, and `recipients={"listIds": [<segment list>], "exclusionListIds": [<suppression list>, <hold list>]}`. Keep the returned `id`. The name carries the release, the persona and the date so a person opening Brevo a week later can tell two drafts apart without opening either.
- **The tool creates drafts only**: it exposes no send and no schedule. Put the proposed send window, **[mid-week, mid-morning in the audience's timezone]**, in the notice and on the row; the person who sends it sets it.
- Read it back with `op="list"` and `campaign_id=<id>`: the status is `draft` and the recipients carry both exclusion lists. The HTML is left out of that response by design, so a missing body there is normal; the body is checked in Brevo.
- **Never `op="test"`**: it sends the campaign for real, to addresses that must already be contacts. Never `brevo_send_email`. A person who wants a test sends it from Brevo.

## 6. Record it and tell the team
- <tool:data_write> on the release campaigns table, upserted on `campaign_key`: the release, the feature, the persona, the segment list, the audience count, the excluded counts by reason, the three subject lines, the body, the Brevo campaign id, the hold list id, the staged timestamp and `status="staged"`.
- <tool:data_write> on the send ledger, **one row per recipient, upserted on `send_key`, written at staging rather than at send**: the ceiling has to see a campaign that is about to go out, not only one that already went. If the person discards the draft, the campaign row moves to `discarded` and the ceiling stops counting that campaign's ledger rows.
- <tool:slack_post_message> once: the release, the persona, the audience count, what was cut and why, the draft's name in Brevo, the proposed window, and one sentence saying it is a draft waiting for a person. The subject lines and the body go in the thread, under the returned `ts`.

## 7. A human reads it and sends it
Everything before this step is reversible; this one isn't. The person opens the draft in Brevo, picks one of the three subject lines, sends a test to themselves and schedules it in the proposed window, then moves the row to `sent_by_person`, or to `discarded` with a reason. A weekly campaign digest can then measure what it did.

## Output
Per release: one unsent Brevo draft per persona with a non-empty audience, each with its hold list attached as an exclusion; one row per campaign in the release campaigns table and one per recipient in the send ledger; one Slack notice naming the release, the persona, the audience count, the counts cut by suppression overlap, ceiling and open deals, the draft's name, the proposed send window and any sender that still needs verifying. A run that found nothing worth announcing says so in one line.