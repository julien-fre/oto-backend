## prerequisite — your klaviyo private API key

in Klaviyo: **Settings → Account → API keys → Create Private API Key**, then paste it into oto (format `pk_…`). Scopes are chosen at creation and **cannot be added later** — a missing scope means a new key:
- reading: **accounts:read** (the "test connection" button needs it), profiles, lists, segments, campaigns, flows, metrics, events — each `:read`
- writing: **profiles:write**, **lists:write**, **events:write**, **subscriptions:write** — leave them out for a read-only key
- byo-only: no shared oto key. The key reaches one Klaviyo account
- the "test connection" button reads the account (`GET /accounts`): it checks the key and its `accounts:read` scope, nothing is written

## usage — profiles, lists and consent

- "who is jane@example.com?" → `klaviyo_profiles(filter='equals(email,"jane@example.com")', additional_fields=["subscriptions"])` — consent per channel included
- "her lists and segments" → `klaviyo_profiles(op="get", profile_id="01H…", include=["lists","segments"])`
- "update her city" → `klaviyo_profiles(op="upsert", attributes={"email": "jane@example.com", "location": {"city": "Lyon"}})` — creates the profile when unknown
- "who joined the newsletter this month?" → `klaviyo_lists(filter='equals(name,"Newsletter")')` for the id, then `klaviyo_lists(op="members", list_id="…", filter="greater-than(joined_group_at,2026-10-01T00:00:00Z)")`
- "subscribe these emails to the newsletter" → `klaviyo_consent(op="subscribe", profiles=[{"email": "…"}], list_id="…")` — read the preview, then the same call with `confirm=True`
- everything → `all_pages=True` (up to `max_pages`, 10 by default): `complete` says whether the walk reached the end; otherwise pass `next_cursor` back as `page_cursor`

## usage — campaigns, flows and performance

- "last month's email campaigns" → `klaviyo_campaigns(filter="equals(status,'Sent'),greater-or-equal(scheduled_at,2026-09-01T00:00:00Z)", sort="-scheduled_at")`
- "how did they perform?" → `klaviyo_metrics()` for the id of **Placed Order**, then `klaviyo_reports(op="campaigns", statistics=["recipients","open_rate","click_rate","conversions","conversion_value"], conversion_metric_id="…", timeframe="last_30_days")`
- "which flows are live?" → `klaviyo_flows(filter="equals(status,'live')")`
- "orders per day in September" → `klaviyo_reports(op="metric", metric_id="…", measurements=["count","sum_value"], since="2026-09-01T00:00:00", until="2026-10-01T00:00:00", timezone="Europe/Paris")`
- "what did this customer do?" → `klaviyo_events(filter='equals(profile_id,"01H…")', sort="-datetime", include=["metric"])`

## note — what reaches real people

the connector **never sends a campaign** and never creates or edits a campaign, flow or template. Four writes may still reach real people, and each returns a **preview** until called again with `confirm=True`:
- **adding profiles to a list** (`klaviyo_lists(op="add")`) starts the flows triggered by that list ("Added to List")
- **recording an event** (`klaviyo_events(op="create")`) starts the flows triggered by its metric — unless `backfill=True`
- **subscribing** (`klaviyo_consent(op="subscribe")`) changes consent; on a double opt-in list Klaviyo emails a confirmation
- **unsubscribing** (`klaviyo_consent(op="unsubscribe")`) is **global** for a profile outside the given list, and for everyone when no list is given — the preview names them. To only leave a list: `klaviyo_lists(op="remove")`, which keeps consent

## note — what is misleading

- **a profile id is not an email**: list membership and `get` take the 26-character id (`01H…`); find it with `filter='equals(email,"…")'`
- **consent jobs and events are asynchronous**: Klaviyo answers 202 and applies them shortly after — read back to check
- **the values reports are slow on purpose**: Klaviyo allows 2 calls per minute and 225 per day — ask every statistic in one call; rates are fractions between 0 and 1
- **campaigns are listed per channel**: email by default, `channel="sms"` for the others
- **a scope cannot be added to a key**: a 403 means creating a new key with the missing scope
- `full=True` returns Klaviyo's raw JSON:API payload when the tightened view is not enough
