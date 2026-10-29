## prerequisite — a Pipecorn API key

create a key in Pipecorn (Settings → API keys) and paste it into oto. Reference: [Pipecorn authentication](https://docs.pipecorn.com/essentials/authentication).
- a **test key** (`pc_test_…`) answers every lookup with sample data and spends nothing: a good way to try the connector before your live key
- your own key first: every lookup then spends **your** Pipecorn credits; each organization or person sets its own key
- the **shared key**, when oto grants it to your org, covers **phone lookups** only — the first step of `waterfall_phone`; emails, the personal email and the balance need your own key
- granting the shared key (`oto_admin_key_grant op=grant`) **requires** a `daily_quota` > 0, counted in **Pipecorn credits** per day (30 a phone found); absent or 0, which would be unlimited, is refused with `grant_quota_required`

## usage — a phone, an email

- "find Jane's mobile" → `pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/", phone_type="mobile")`
- "her work email" → `pipecorn(find=["email"], linkedin_url=…)`, or without a profile `pipecorn(find=["email"], firstname="Jane", lastname="Doe", domain="acme.com")`
- both at once → `find=["email", "phone"]`: one call, each result billed when found
- phones from some countries only → `phone_country_codes=["FR", "BE"]`
- "how many credits are left?" → `pipecorn(op="credits")`: `credits.enrichment` is what lookups spend
- for a list of people, prefer `waterfall_phone` (Pipecorn first, then your other providers) or its table mode `waterfall_phone_act(op="update_rows")`

## note — what can mislead

- you pay **per result found**, in Pipecorn credits: 3 a work email, 9 a personal email, 30 a phone — and a phone only when a **mobile** is found; a miss costs nothing. Each answer says what it cost (`pipecorn_credits`)
- a miss is **not an error**: empty fields, `phone_status: "not_found"`
- `phone_type="mobile"` keeps looking until a provider has a mobile and drops landlines; without it, a landline can come back, unbilled
- a number your Pipecorn workspace suppressed comes back as the text `"Contacts not shared"`, not a number
- the **personal email** needs the key owner's consent in Pipecorn's settings (refused otherwise, nothing spent)
- a lookup is **never retried** automatically
