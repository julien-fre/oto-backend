## prerequisite — an emailbison api token and the address of your instance

in EmailBison, **Settings → Developer API → New API Token**, then paste the token into oto with **the address you log in to** (e.g. `https://send.your-agency.com`, no trailing slash). Reference: the **API Reference** of your instance (`<your address>/api/reference`) and [docs.emailbison.com](https://docs.emailbison.com).
- ⚠️ choose an **`api-user`** token: it stays in the workspace it was made in; a `super-admin` token follows whichever workspace its maker has switched to
- a token **reads and acts**: EmailBison has no read-only token
- the address must be **https** and public: oto refuses any other before sending anything
- your own token only: no shared oto key — the campaigns, leads and mailboxes are your own
- the "test connection" button reads the token's user and workspace: no side effect

## usage — campaigns, replies, leads, webhooks

`emailbison` reads, `emailbison_act` acts; each call names its function in `op`.

- "how are my campaigns doing" → `emailbison(op="list_campaigns", status="active")`, then `emailbison(op="get_campaign_stats", campaign_id=…, start_date="2026-11-01", end_date="2026-11-30")` (per step in `sequence_step_stats`)
- "the workspace this month" → `emailbison(op="get_workspace_stats", start_date="2026-11-01", end_date="2026-11-30")`; day by day → `get_daily_stats`
- "the new interested replies" → `emailbison(op="list_replies", folder="inbox", status="interested", read=0)`, the whole conversation with `emailbison(op="get_reply_thread", reply_id=…)`
- "answer that reply" → `emailbison_act(op="reply_to_email", reply_id=…, reply_all=true, message="…", content_type="text")` — sent at once
- "mark it interested" → `emailbison_act(op="mark_reply_interested", reply_id=…)` (fires the workspace's webhooks unless `skip_webhooks=true`)
- "add these people to the webinar campaign" → `emailbison_act(op="create_leads", leads=[{"first_name": "…", "email": "…", "custom_variables": [{"name": "event", "value": "…"}]}])` for their ids, `emailbison(op="list_campaigns", search="webinar")` for the campaign, then `emailbison_act(op="add_leads_to_campaign", campaign_id=…, lead_ids=[…])`
- "stop emailing this person" → `emailbison_act(op="stop_future_emails", campaign_id=…, lead_ids=[…])`, or for good `blacklist_email` / `blacklist_domain`
- "wake an agent on each reply" → `emailbison_act(op="create_webhook", name="…", url="<the agent's https hook>", events=["lead_replied"])`; `emailbison(op="list_webhooks")` lists them, `delete_webhook` removes one

## note — sending, pages and limits

- ⚠️ **these ops send email**: `reply_to_email` (at once), `add_leads_to_campaign` / `add_lead_list_to_campaign` into an active campaign (synced every 5 minutes), `resume_campaign`
- ⚠️ **a webhook posts lead data** (emails, replies) to its address: only an address the workspace trusts
- **pages**: lists answer 15 items and `meta.next_cursor`; pass it back as `cursor` (absent = last page); `list_lead_lists` takes `page`
- **lean answers**: a reply's raw body, headers and attachment download address are never shown; lists and threads drop the HTML body (`get_reply` keeps it)
- a lead is named by its **id or its email** in `get_lead`, `update_lead`, `list_lead_sent_emails`
- not covered: sending accounts' creation and warmup, campaign creation and sequence editing, composing a new email or forwarding, CSV imports, workspace administration and API tokens, deletions other than a webhook
