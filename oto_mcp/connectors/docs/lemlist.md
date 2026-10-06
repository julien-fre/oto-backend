## prerequisite — lemlist api key

create an API key in [lemlist](https://app.lemlist.com) (Settings → Integrations → API), then paste it into oto (account / connectors page).
- everyone sees THEIR data: your own key is required

## usage — the whole lemlist API

the connector mirrors the **141 documented routes**, without exception. by family:

**campaigns** — `lemlist_list_campaigns`, `lemlist_campaign`, `lemlist_sequence`, `lemlist_schedule`, `lemlist_get_campaign_stats`
- "create a campaign "Q4 outbound", add an email step at D+3"
- "what is blocking the launch of this campaign?" (missing sender, DNS, daily limit)
- "set the sending window to 8am-12pm Monday to Thursday", "duplicate it for the NL team"
- "stats for campaign X", "compare my 5 campaigns this quarter"

**leads** — `lemlist_create_lead`, `lemlist_lead`, `lemlist_enrich*`
- "add this lead, find their email and verify it"
- a lead already in a campaign is not an error: `lemlist_create_lead` returns `created: false` with `reason` (`already_in_other_campaign` / `already_in_campaign`) — the contact is already taken, count it as such
- a created lead does not say whether it goes out: `lemlist_create_lead` returns `review_state: "unknown"` (lemlist does not say, and `isPaused` is ruled out because it does not distinguish review from sending). To find out, use `lemlist_campaign(op="reports")`: if `reviewedCount` or `inSequenceLeadCount` go up with the addition, the lead goes out
- "pause this lead on all campaigns", "mark them as interested"
- "import the leads from HubSpot filter X into this campaign"

**lemlist CRM** — `lemlist_contact`, `lemlist_company`, `lemlist_team(op="fields")`
- ⚠️ a **contact** is not a **lead**: the lead is a person's copy INSIDE a campaign, the contact is the person themselves. this is the most costly confusion here.

**inbox** — `lemlist_inbox` (conversations, drafts, labels) and `lemlist_inbox_send`
**unsubscribes** — `lemlist_unsubscribe`: ⚠️ **three distinct lists** (v1 emails/domains, v2 variables, a contact's do-not-contact flag); writing to one does not write to the others
**signals** — `lemlist_watchlist`: companies that are hiring, fundraising, job changes…
**the rest** — `lemlist_task`, `lemlist_database` (shared base + personas), `lemlist_team`, `lemlist_mailbox` (SMTP/IMAP connection + lemwarm), `lemlist_deliverability`, `lemlist_webhook`, `lemlist_get_activities`

## note — what sends, and what is hidden by default

four tools send or **arm** sending. all four are **hidden by default** — they remain callable, you just have to enable them (`oto_enable_tool <name>`):

- `lemlist_campaign_start` — starting the campaign runs the sequence for all its launched leads
- `lemlist_launch_lead` — take a lead out of manual review
- `lemlist_inbox_send` — **direct** email / LinkedIn / WhatsApp: no campaign, no sequence, no review in front of them, the message goes out
- `lemlist_campaign_auto_review` — sends nothing itself, but makes every lead **added** afterwards go out: with it, `lemlist_create_lead` becomes a send

everything else works on data or on a campaign that has nothing to send yet.

⚠️ **a campaign created by the API is born `state=running`**, contrary to what its `status` ("draft") suggests — verified live on 31/08. it sends nothing as long as no lead is launched, but `lemlist_campaign(op="start")` answers "already running" and `op="pause"` is the real switch. to build one calmly: create, then pause. (a **duplicated** campaign, on the other hand, is born paused.)

pause ≠ recall: pausing stops progression, not what is already scheduled.

two surfaces send **indirectly**, and stay visible by saying so: a watch list set to `push_to_campaign` feeds a campaign on its own, and `lemlist_mailbox(op="lemwarm_start")` sends — but within the warm-up network, never to a prospect.

## note — lemlist API pitfalls

- **deleting a lead ≠ unsubscribing them**, and lemlist serves both through the same route, the default being the soft one. here the two ops are named separately (`op="delete"` vs `op="unsubscribe"`).
- **`lemlist_lead(op="pause")` without `campaign_id` pauses the lead on ALL campaigns**, not just one.
- **`lemlist_contact(op="list_manage")` ADDS by default**; `action="remove"` removes.
- deleting a step is refused while the campaign is running — pause it first.
- enrichments spend credits (`lemlist_team(op="credits")` counts them).

## note — doc↔API gaps found live (31/08/2026)

the connector already absorbs them; this is here to understand an error message, not to act on.

- `lemlist_task(op="create")` requires `record_id` (the contact or the lead), which the lemlist doc says is optional.
- `lemlist_watchlist(op="create")`: read `op="filters"` first — each type has its own mandatory filters, values must come from `op="filter_values"`, and numbers travel as strings.
- `lemlist_campaign(op="export_leads")` filters on `state="all"` by default: lemlist's default returns an empty list that reads as "no leads".
- what this account does not allow (plan limit, not a bug): LinkedIn steps (`Upgrade your plan`), the CRM endpoints (`crm_filters`, `crm_users`, `import_crm` → "Endpoint not available" without a CRM integration), and a watch list's history (beta not enabled).
