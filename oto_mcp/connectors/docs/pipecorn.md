## prerequisite — a Pipecorn API key

create a key in Pipecorn (Settings → API keys) and paste it into oto. Reference: [Pipecorn authentication](https://docs.pipecorn.com/essentials/authentication).
- a **test key** (`pc_test_…`) answers every lookup with sample data and spends nothing: a good way to try the connector before your live key
- your own key first: every lookup then spends **your** Pipecorn credits; each organization or person sets its own key
- the **shared key**, when oto grants it to your org, covers every lookup — phones, work and personal emails, profiles, companies, technologies, job postings — but not the **balance**
- the shared key carries **no per-org daily cap**: your Tulina credits bound what you spend

## usage — a phone, an email

- a lookup is **asynchronous**: `pipecorn(...)` submits it and answers at once with an `enrichment_id`; `pipecorn(op="result", enrichment_id=…, find=[…])` reads it (free) — `done: false` while Pipecorn is still looking, read again a few seconds later (usually under a minute)
- "find Jane's mobile" → `pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/", phone_type="mobile")`, then `op="result"`
- "her work email" → `pipecorn(find=["email"], linkedin_url=…)`, or without a profile `pipecorn(find=["email"], firstname="Jane", lastname="Doe", domain="acme.com")`
- both at once → `find=["email", "phone"]`: one lookup, each result billed when found; add `"personal_email"` for a personal address
- **synchronous**: `pipecorn(op="find", …)` is the same lookup in one call (up to ~30 s), with each value found listed in `results` — `{kind: phone | email | personal_email, value, type or status}`
- phones from some countries only → `phone_country_codes=["FR", "BE"]`
- "how many credits are left?" → `pipecorn(op="credits")`: `credits.enrichment` is what lookups spend
- "what does Jane do now?" → `pipecorn(op="person", linkedin_url=…)`: current title and company (with its domain), past positions, tenure — no email or phone
- "tell me about acme.com" → `pipecorn(op="company", domain="acme.com")`: industry, headcount and its growth, department headcount, revenue range, headquarters, funding, web traffic
- "what does acme.com run?" → `pipecorn(op="stack", domain="acme.com")`: technologies with a confidence
- "is acme.com hiring salespeople in France?" → `pipecorn(op="jobs", domain="acme.com", job_title_keywords=["sales"], job_country_codes=["FR"], limit=20)`: open postings with their hiring team — billed per job, so bound `limit`; cheaper per job than TheirStack
- for a contact, prefer the waterfalls: Pipecorn is the first step of `waterfall_phone` and `waterfall_email` (submitted and read for you, up to 20 s, then your other providers), and a step of `waterfall_person`, `waterfall_company`, `waterfall_tech`. On a table (`update_rows`), the phone and email steps run synchronously (`op="find"`)

## usage — at scale, a recipe over a table

a recipe runs `op="find"` for every row of a contacts table and writes one row per value found into a target table — no model reads the results:
- `"tool": "pipecorn"`, `"arguments": {"op": "find", "find": ["phone", "email"], "linkedin_url": "{{row.linkedin}}", "phone_type": "mobile"}`
- `"for_each": {"datastore": <contacts table>, "status_column": "pipecorn_status"}` — a row done is never paid again; a contact Pipecorn knows nothing about is marked `empty`
- `"source": {"items": "results"}`, `"map": {"kind": "kind", "value": "value", "type": "type", "status": "status"}`
- `"key": {"column": "contact_key", "template": "{{row.linkedin|linkedin_slug}}::{{item.kind}}::{{item.value}}"}`
- `"limits": {"max_units": …}` counts **Pipecorn credits** (33 for a mobile and an email): at the cap the run stops cleanly; a new run carries on without paying the rows already done
- each run has a 30 s clock: a large table takes several runs (`resume`), each one picking up where the last stopped
- to write back into the SAME table, use `waterfall_phone_act` / `waterfall_email_act`: `op="update_rows"` runs Pipecorn synchronously; for hundreds of rows, `update_rows` with `asynchronous=true` submits them all in a call or two (up to 200 rows a call), then `op="collect_rows"` reads the answers back — the fastest way at scale

## note — what can mislead

- you pay **per result found**, in Pipecorn credits: 3 a work email, 9 a personal email, 30 a phone (only a **mobile**), 1 a profile or a company, 1 per job posting; a technology stack is 3 per request. A miss costs nothing. A contact lookup is billed when it finishes. The finished result says what it cost (`pipecorn_credits`); reading it again costs nothing more
- pass the same `find` to `op="result"` as to the submission: only what was asked is counted
- a lookup can end `failed` when the credits ran out while it ran: nothing found, nothing billed
- a miss is **not an error**: empty fields, `phone_status: "not_found"`
- `phone_type="mobile"` keeps looking until a provider has a mobile and drops landlines; without it, a landline can come back, unbilled
- a number your Pipecorn workspace suppressed comes back as the text `"Contacts not shared"`, not a number
- the **personal email** needs the key owner's consent in Pipecorn's settings (refused otherwise with a 422, nothing spent) — on the shared key, the consent is Tulina's
- a lookup is **never retried** automatically
