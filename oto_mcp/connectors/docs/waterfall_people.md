## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — apollo, lemlist (its people database), ai ark (`aiark`), apify (for the linkedin employees), serper (for google)
- with the default order, providers you hold your **own** key for run first, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_people` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `google,linkedin_employees,apollo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — the people at a company

one company per call, stopping at the first source that returns at least one matching person.
- `waterfall_people` — default order: apollo (domain, free — masked last names don't stop the cascade, they're its fallback) → lemlist (its people database, by domain or linkedin page — full names, current titles, linkedin urls and a pattern-guessed email (`email_status: guessed`)) → google (linkedin profiles on google, full names) → aiark (domain) → linkedin_employees (the company's linkedin page)
- lemlist's database spends no lemlist credits, but each page it reads is one call of the key's search quota; with your own (or your org's) lemlist key it runs FIRST, ahead of apollo
- pass `roles` (e.g. `["sales", "CEO"]`) to keep only the decision-makers you want, `country` for a local subsidiary of a global domain, `limit` (≤ 25)
- apollo's search hides last names: reveal someone with `apollo_match_person(person_id=<apollo_id>)`, never by name + company
- no linkedin page yet? `waterfall_company` finds it (from the website, then by name)
- then, per person: `waterfall_email` and `waterfall_phone`

## usage — qualify before you spend

- `qualify=` takes a Jev rubric (same shape as `jev_ask`'s `questions`): each person found gets typed answers with probabilities, in ONE call for micro-dollars, instead of being read and judged by the agent
- `qualify_min=0.7` keeps only what passes every yes/no question ; a provider whose people ALL fall short does not end the search, the next one is tried; the rejected come back under `unqualified` (name, title, URL, answers), `show_unqualified=true` for their full records
- only the fields a fit needs are sent to Jev (title, seniority, industry, size…), never a name, email, phone or profile URL
- no Jev key or no time left: results come back unqualified and the receipt's `qualification` says why
- for a whole list, `waterfall_people_act(op="update_rows")` runs it over a table's rows on the server: only rows with an empty `status_column` are taken, rows with the same inputs share one lookup, the find is written into each row and the answer is counts only — call again until `remaining` is 0; the asynchronous steps are left out there
