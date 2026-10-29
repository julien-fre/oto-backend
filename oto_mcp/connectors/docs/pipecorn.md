## prerequisite — a Pipecorn API key

create a key in Pipecorn (Settings → API keys) and paste it into oto. Reference: [Pipecorn authentication](https://docs.pipecorn.com/essentials/authentication).
- a **test key** (`pc_test_…`) answers every lookup with sample data and spends nothing: a good way to try the connector before your live key
- your own key first: every lookup then spends **your** Pipecorn credits; each organization or person sets its own key
- the **shared key**, when oto grants it to your org, covers everything but the **personal email** and the **balance**: phones, work emails, profiles, companies, technologies, job postings
- the shared key carries **no per-org daily cap**: your Tulina credits bound what you spend

## usage — a phone, an email

- a lookup is **asynchronous**: `pipecorn(...)` submits it and answers at once with an `enrichment_id`; `pipecorn(op="result", enrichment_id=…, find=[…])` reads it (free) — `done: false` while Pipecorn is still looking, read again a few seconds later (usually under a minute)
- "find Jane's mobile" → `pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/", phone_type="mobile")`, then `op="result"`
- "her work email" → `pipecorn(find=["email"], linkedin_url=…)`, or without a profile `pipecorn(find=["email"], firstname="Jane", lastname="Doe", domain="acme.com")`
- both at once → `find=["email", "phone"]`: one lookup, each result billed when found
- phones from some countries only → `phone_country_codes=["FR", "BE"]`
- "how many credits are left?" → `pipecorn(op="credits")`: `credits.enrichment` is what lookups spend
- "what does Jane do now?" → `pipecorn(op="person", linkedin_url=…)`: current title and company (with its domain), past positions, tenure — no email or phone
- "tell me about acme.com" → `pipecorn(op="company", domain="acme.com")`: industry, headcount and its growth, department headcount, revenue range, headquarters, funding, web traffic
- "what does acme.com run?" → `pipecorn(op="stack", domain="acme.com")`: technologies with a confidence
- "is acme.com hiring salespeople in France?" → `pipecorn(op="jobs", domain="acme.com", job_title_keywords=["sales"], job_country_codes=["FR"], limit=20)`: open postings with their hiring team — billed per job, so bound `limit`; cheaper per job than TheirStack
- for a contact, prefer the waterfalls: Pipecorn is the first step of `waterfall_phone` and `waterfall_email` (submitted and read for you, up to 20 s, then your other providers), and a step of `waterfall_person`, `waterfall_company`, `waterfall_tech`. Their table modes leave the asynchronous phone and email lookups out

## note — what can mislead

- you pay **per result found**, in Pipecorn credits: 3 a work email, 9 a personal email, 30 a phone (only a **mobile**), 1 a profile or a company, 1 per job posting; a technology stack is 3 per request. A miss costs nothing. A contact lookup is billed when it finishes. The finished result says what it cost (`pipecorn_credits`); reading it again costs nothing more
- pass the same `find` to `op="result"` as to the submission: only what was asked is counted
- a lookup can end `failed` when the credits ran out while it ran: nothing found, nothing billed
- a miss is **not an error**: empty fields, `phone_status: "not_found"`
- `phone_type="mobile"` keeps looking until a provider has a mobile and drops landlines; without it, a landline can come back, unbilled
- a number your Pipecorn workspace suppressed comes back as the text `"Contacts not shared"`, not a number
- the **personal email** needs the key owner's consent in Pipecorn's settings (refused otherwise, nothing spent)
- a lookup is **never retried** automatically
