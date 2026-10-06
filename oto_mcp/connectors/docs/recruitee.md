## prerequisite — your recruitee api token + company id

recruitee requires **two fields**:
- `api_token` — your personal api token (recruitee, **settings → apps & plugins → personal API tokens**)
- `company_id` — your recruitee company identifier (visible in your workspace url, e.g. `recruitee.com/c/<company_id>`)
enter both in your [connector keys](https://manage.oto.cx/).
- vendor docs: [recruitee.com](https://www.recruitee.com)

## usage — what you can do

drive your recruitee ats: a job = an **offer**, a candidate is attached to offers.
- "list the candidates for job 12" → `recruitee_candidates` (filters `offer_id`, `query` by name/email), details → `recruitee_candidate`
- "create a candidate and attach them to offer 12" → `recruitee_create_candidate` (`offer_ids`)
- "add a note to this candidate" → `recruitee_add_note`
- "list my active offers" → `recruitee_offers` (`scope` active/archived, `kind` job/talent_pool), details → `recruitee_offer`
