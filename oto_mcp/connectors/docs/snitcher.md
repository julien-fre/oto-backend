## prerequisite — snitcher personal access token

generate a Personal Access Token in Snitcher (app → Settings → Account → API → Generate New Token — see the [REST API docs](https://docs.snitcher.com/product/rest-api/introduction)), then paste it into oto.
- byo-only: no shared oto key — a PAT is bound to ONE Snitcher account
- rate limit: 60 requests/minute per token (429 beyond that)

## usage — which companies visit your site

snitcher identifies the COMPANIES behind your site's anonymous traffic, in 5 tools:
- "which workspaces do I have?" → `snitcher_workspace(op="list")` — **always start there**: the returned `workspace_uuid` is required by all the other tools
- "which companies visited the site this week?" → `snitcher_organisation(workspace_uuid="...", op="list", date_from="2026-08-17")`
- "companies seen in the last 30 days with more than 5 pageviews" → `snitcher_organisation(op="search", filters={"operator": "AND", "conditions": [{"field": "last_seen", "comparison": "less_than_x_units_ago", "value": 30, "unit": "day"}, {"field": "pageviews", "comparison": "greater_than", "value": 5}]})` — ⚠️ FLAT conditions only (the spec's nested groups are refused for real, 422), and fields limited to visit behavior: last_seen, first_seen, tag, sessions, pageviews, time_on_site, url, referrer, source — NOT firmographics (name/industry/size → go through `op="list", name="..."` or a segment)
- "what does this company do on the site?" → `snitcher_session(workspace_uuid="...", organisation_uuid="...")` — each session carries an `events` array: pageviews (with time_on_page), form submissions (WITH the field values), custom `track` events, clicks, downloads
- "all of yesterday's sessions" → `snitcher_session(workspace_uuid="...", date="2026-08-22")` — without `organisation_uuid`, `date` or `date_from` is required
- "who are the decision-makers at this company?" → `snitcher_contact(op="list", organisation_uuid="..." | domain="acme.com")`
- "reveal this contact's email" → `snitcher_contact(op="reveal_email", contact_uuid="...")` — ⚠️ **spends a Snitcher credit**, confirm intent first
- "tag this company 'hot lead'" → `snitcher_workspace(op="create_tag", tag_name="hot lead")` then `snitcher_organisation(op="tag", organisation_uuid="...", tag_name="hot lead")`
- "which segments exist?" → `snitcher_workspace(op="segments")` — their uuids filter organisations and sessions
- "note this account's tier" → `snitcher_custom_field(op="set", organisation_uuid="...", key="account_tier", value="enterprise")` — `op="set_many"` sets up to 50 fields at once, unknown keys are created automatically (type inferred)

## note — ⚠️ what costs, what destroys, what excludes

- `snitcher_contact(op="reveal_email")` is the ONLY paid call (credits) — everything else is a read or a free write (tags, custom fields, workspace admin)
- `snitcher_workspace(op="delete")` destroys the workspace AND its visit history — irreversible, to be explicitly confirmed with the user
- `date` (one day) and `date_from`/`date_to` (a range) are mutually exclusive wherever both exist
- `visible_in_spotter=true` on a custom field exposes its values to any script of the tracked site (Spotter response) — off by default, leave it off unless explicitly needed
- emptying a multi-select does NOT go through `op="set"` with an empty list (refused by the API) — use `op="clear"`
- **live-tested on 2026-08-24** with a real trial token (test workspace): 24 of the 27 endpoints exercised — all the reads, the full tag cycle (create → attach → verified on the organisation → detach), the full custom-field cycle (definitions + values, cleaned up afterwards); not exercised: reveal_email (credit), create/delete workspace, invite
- the response shape VARIES by endpoint (confirmed live): lists carry Laravel pagination at the root level (`success`/`current_page`/`total`/`data`), gets return the bare object with no envelope, tags return `{success, message}`, DELETEs return an empty body — ⚠️ NEVER assume `result["data"]` everywhere: `get_organisation` for example returns the bare object, `result["data"]` raises a KeyError there
- **the subtlest trap is IN `snitcher_custom_field`**: `op="set"` (one field, PUT) returns the BARE value object, but `op="set_many"` (several fields, PATCH) returns `{"success", "data": [...]}` — same intent ("set a value"), different envelope depending on the verb. Each `op=` has its form documented in the tool description, to be re-read before parsing the return rather than guessing
- `snitcher_custom_field(op="set_many")` does create unknown keys automatically, type inferred (confirmed: a `42` created a `number` field); `op="values"` also returns the fixed SYSTEM fields (name, website, description…, `source: "fixed"`) alongside the custom ones
- `snitcher_contact(op="list", domain="...")` works for any company, not just identified visitors (confirmed: 25 contacts on a third-party domain) — emails stay `"[not-revealed]"` until the paid reveal has been done
