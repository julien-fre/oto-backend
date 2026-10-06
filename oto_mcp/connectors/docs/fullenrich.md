## prerequisite — get a fullenrich key

create an api key in the api settings of your [fullenrich](https://app.fullenrich.com) account.
- paste it into your oto connectors on `/account` — fullenrich is **byo** (everyone brings their own key)
- **pay-per-result** billing: 10 credits/phone, 1/work email, 3/personal email, nothing if no data is found

## usage — waterfall enrichment (20+ sources)

finds a contact's phones and emails in a cascade across 20+ providers (~70% hit rate on phone).
- `fullenrich_enrich_linkedin` — submits a **bulk** job (1 to 100 contacts: first/last name + linkedin slug + company), returns immediately with an `enrichment_id`
- `fullenrich_result` — fetches the result: call again after `retry_after_s` until `done` (a job takes ~30s to 4 min). Pass the `submitted_at` returned at submission: beyond 20 min, the response says to stop (`verdict`) — do not resubmit, it would be billed twice. A cancelled, unknown or expired job is a named refusal, not a status to poll. Polling does not consume the platform quota.
- returns phones, work and personal emails, title and location per contact
