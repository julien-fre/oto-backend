## prerequisite — waalaxy API key

generate an API key in Waalaxy (app → Settings → [CRM Sync](https://app.waalaxy.com/settings/crm-sync) → Generate API key — see the [API doc](https://docs.waalaxy.com/introduction)), then paste it into oto (format `zpka_…` — the doc shows `wa_live_…`, the real keys are Zuplo `zpka_` keys). It is only displayed once (revoke + regenerate in the same place).
- Advanced or Business plan required — the API does not exist on lower plans
- byo-only: no shared oto key — one key = ONE Waalaxy seat, hence ONE LinkedIn account

## usage — push prospects into waalaxy

the Waalaxy API is **import-only**: it is for feeding Waalaxy, not for reading it. 3 tools:
- "which lists do I have?" → `waalaxy_prospect_list()` — **always start here**: the `_id` returned is the `prospect_list_id` required by the import
- "which campaigns are running?" → `waalaxy_campaign()` — only running/paused campaigns are visible; their `_id` is the `campaign_id`
- "add this LinkedIn profile to list X" → `waalaxy_prospect(op="add", prospect_list_id="...", prospect={"url": "https://www.linkedin.com/in/jane-doe"})`
- "import these 40 leads into list X and start them in campaign Y" → `waalaxy_prospect(op="add", prospect_list_id="...", campaign_id="...", prospects=[{"url": "...", "customProfile": {"firstName": "Jane", "lastName": "Doe", "email": "jane@acme.com", "company": {"name": "Acme"}}, "customVariables": [{"label": "pain", "value": "…"}]}, ...])` — max 100 per call, a single HTTP call
- "show me what would go out without sending it" → same call with `dry_run=true`: returns the exact payload, zero Waalaxy calls
- typical pattern: sourcing elsewhere (Apollo, Pharow, an oto datastore…) → `waalaxy_prospect(op="add")` → Waalaxy runs invitations/messages on its own

## note — pitfalls & limits

- **Waalaxy answers 200 even if ALL the prospects failed**: read `failed` in the receipt (`{total, imported, enrolled, failed: [{index, url, code, message}], items: [{index, url, importCode, prospect_id, publicIdentifier}]}`), never the HTTP status alone. Frequent codes: `duplicated_prospect` (already in another list — pass `move_duplicates_to_other_list=true` or `can_create_duplicates=true`, the latter requires the account's import_duplicates permission), `max_limit_crm` (the plan's CRM quota reached), `already_in_campaign`, `cant_add_prospect_campaign_is_archived`
- `customProfile` only fills the empty fields of an existing prospect, unless `should_overwrite_custom_profile_data=true`
- `customVariables[].value` ≤ 1000 characters (refused before sending)
- no reading of prospects, no deletion, no access to the inbox or to campaign stats through the API — all of that stays in the app; feedback (replies, connections) comes back through the "CRM Sync" webhooks configured INSIDE a Waalaxy campaign, not through this connector
- Waalaxy enriches the profile from LinkedIn on import (headline, region, company website, birthday…) — `customProfile` is mostly for the fields LinkedIn does not have (email, phone)
- **tested live on 2026-08-26** with a real key: probe, both lists, dry_run, a real import (success) and its duplicate (`duplicated_prospect`, `message` null in practice); not exercised: campaign enrollment and the duplicate flags. The real base URL is developers.waalaxy.com (api.waalaxy.com, cited in the doc's prose, does not resolve); the lists return more fields than the schema (`doNotContact`, `createdAt`…)
