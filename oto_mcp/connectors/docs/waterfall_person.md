## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — your connected linkedin account (free), lemlist (its database and its enrichment), ai ark (`aiark`), apollo, forager, cognism, lusha, lemlist
- with the default order, the free steps run first, then lemlist's database on your **own** lemlist key (no credits), then the other providers you hold your own key for, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_person` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `linkedin,aiark,apollo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a person's current role and company

one person per call, stopping at the first provider that returns a title or a company.
- `waterfall_person` — default order: linkedin (your own connected account, free) → lemlist_db (lemlist's people database: current title, company, seniority, location, and a pattern-guessed email marked `email_status="guessed"`) → pipecorn (linkedin url, or name + company) → aiark → apollo → forager → cognism → lusha → lemlist
- give the linkedin url when you have it: it opens every step; an email or name + domain opens apollo, cognism, lusha and lemlist; name + domain opens lemlist_db
- lemlist's enrichment (`lemlist`) answers asynchronously (see `pending` in the response)
- lemlist's database bills no lemlist credits: each call spends one call of the lemlist key's search quota
- `dry_run=true` shows the plan without spending anything

## usage — qualify before you spend

- `qualify=` takes a Jev rubric (same shape as `jev_ask`'s `questions`): the person found gets typed answers with probabilities, in ONE call for micro-dollars, instead of being read and judged by the agent
- `qualify_min=0.7` keeps only what passes every yes/no question (the record stays, with `qualification.passed`)
- only the fields a fit needs are sent to Jev (title, seniority, industry, size…), never a name, email, phone or profile URL
- no Jev key or no time left: results come back unqualified and the receipt's `qualification` says why
- for a whole list, `waterfall_person_act(op="update_rows")` runs it over a table's rows on the server: only rows with an empty `status_column` are taken, rows with the same inputs share one lookup, the find is written into each row and the answer is counts only — call again until `remaining` is 0; the asynchronous steps are left out there
