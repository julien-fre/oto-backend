## prerequisite — theirstack api key

create an API key in TheirStack (Settings → API keys — see the [authentication docs](https://theirstack.com/en/docs/api-reference/authentication)), then paste it into oto.
- byo-only: no shared oto key, each account/org consumes its own credits

## usage — who is hiring what, and with which tools

theirstack aggregates the job postings published by companies (career sites + job boards, 100+ countries) and infers the technologies they use (ERP, CRM, e-commerce…).
- "is this company hiring right now? which roles?" → `theirstack_jobs_search(company_names=["Exact Name"])` (postings from the last 90 days by default)
- "which French wholesalers use a given ERP?" → `theirstack_companies_search(company_country_code_or=["FR"], extra={"company_technology_slug_or": ["sap"]})`
- "tech profile + headcount for these companies" → `theirstack_companies_search(company_names=[...])` (returns name, domain, headcount, industry, technologies)
- need the full record (job description, salaries, hiring team, revenue…) → `full=True`

## note — credits, coverage and exact names

- credits are counted per COMPANY record returned: one company credit unlocks all its postings + technologies + firmographics; `limit` caps the spend, `metadata.truncated_*` says what wasn't returned for lack of credits. TheirStack returns no counter of credits consumed: every response carries `credits_estimes` (1 per posting, 3 per company returned), an estimate based on the published rate card; the real balance is read in the TheirStack dashboard
- partial coverage on small companies (≈ 8% of the small French wholesalers seen in the pilot): a `data: []` response is NORMAL, not an error — no point retrying
- `company_names` is an EXACT, case-sensitive match; to broaden, pass `company_name_case_insensitive_or`, `company_name_partial_match_or` or `company_domain_or` in `extra`
- `extra` opens the full vendor DSL (~110 job filters, ~60 company filters): see the [API reference](https://theirstack.com/en/docs/api-reference)
