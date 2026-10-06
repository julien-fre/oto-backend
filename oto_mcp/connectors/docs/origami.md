## prerequisite — origami api key

create an API key in Origami (Settings → API keys; it starts with `og_live_` — see the [authentication docs](https://docs.origami.chat/authentication)), then paste it into oto.
- byo-only: enrichment credits and sends are those of the org's Origami account
- to send, an email and/or LinkedIn account must be connected in Origami — otherwise the launch answers `blocked.missingChannels` and nothing goes out

## usage — from leads to launching an email + LinkedIn campaign

origami keeps lead tables and has its agent draft then send multichannel campaigns.
- "which workspaces / which tables?" → `origami_workspaces`, `origami_tables(op="list")`
- "create a table from this CSV" → `origami_upload_csv(workspace_id, "leads.csv", csv_text)` (`dry_run=True` shows the first rows)
- "add / update these contacts in the table" → `origami_tables(op="columns")` to read the SLUGS, then `origami_rows(op="upsert", rows=[{slug: value}], match_columns=["email"])`
- "read the rows" → `origami_rows(op="list", max_pages=…)` (follows `nextCursor` server-side)
- "draft a campaign on this table" → `origami_campaign_create(table_id, instructions)` then `origami_run_get(agent_id, run_id)` until `status != "running"`
- "launch it" → `origami_campaign_launch(campaign_id, dry_run=False)` — the default `dry_run=True` only previews
- "where does it stand?" → `origami_campaigns(op="stats" | "people")`, `origami_sequences(workspace_id=…)`
- "pause / resume / delete" → `origami_campaign_pause`, `origami_campaign_resume`, `origami_campaign_delete(confirm=True)`

## note — what sends, what costs, what traps

- **launching sends for real** (emails + LinkedIn messages to real people): review the enrolled people and the text BEFORE `dry_run=False`; there is no recall
- `origami_campaign_create` with `block_prior_contacts=True` (default) excludes anyone previously enrolled, EVEN in a deleted draft that was never sent — pass False only if those enrollments never sent to anyone
- the `block_prior_contacts` / `block_active_duplicates` settings are REQUESTED, not guaranteed: Origami ignored them on campaigns created by the call. Re-read `origami_campaigns(op="get")` → `settings` after the run; restating them in plain words in `instructions` made them stick in practice; otherwise, toggle by hand in Origami
- a run refused with `aucune_action` can leave a "Ready to launch" draft in the Origami interface that is invisible to the API: retry ONCE, and have a human check the campaign list before retrying again
- row keys are the **slugs** of the input columns (with dashes), not the displayed names — an unknown slug is rejected (400 UNKNOWN_FIELDS)
- `enrich=False` by default on upsert: enrichment spends credits, it must be requested explicitly
- deletion happens in two steps; the tool re-reads the campaign and only says "deleted" on a 404
- there is no global campaign list: list by table, or use `origami_sequences(workspace_id=…)` which follows `nextCursor` (pages of 50, `max_pages=10` by default) and returns `campaign_ids`, the DISTINCT campaigns seen — a single page makes you think there is one where there are four; `truncated: true` = more remain, pass `cursor` again
- `origami_upload_csv` returns `table_id` / `table_slug` at the top level (the id of the created table conditions the upsert and the campaign that follow) and `error` if Origami rejected the file
