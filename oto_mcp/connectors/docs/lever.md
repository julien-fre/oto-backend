## prerequisite — your lever api key

you need a **lever api** key.
- in lever, go to **settings → integrations and API → API credentials** and generate a key
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [lever.co](https://www.lever.co)
- ⚠️ writes (creating a candidate, adding a note) require a `perform_as` = the id of a lever user, fetched via `lever_users`

## usage — what you can do

drive your lever ats: a candidate = an **opportunity**, a job = a **posting**.
- "list the candidates of posting abc" → `lever_opportunities` (filters `posting_id`, `stage_id`, `email`; `expand` to unfold stage/owner)
- "show opportunity xyz" → `lever_opportunity`
- "add a note to this opportunity" → `lever_add_note` (with `perform_as`, see `lever_users`)
- "which of my jobs are published?" → `lever_postings` (`state` published/closed/draft…); pipeline stages → `lever_stages`
