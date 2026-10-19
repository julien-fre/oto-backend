## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — apollo, ai ark (`aiark`), apify (for the linkedin employees), serper (for google)
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
- `waterfall_people` — default order: apollo (domain, free — masked last names don't stop the cascade, they're its fallback) → google (linkedin profiles on google, full names) → aiark (domain) → linkedin_employees (the company's linkedin page)
- pass `roles` (e.g. `["sales", "CEO"]`) to keep only the decision-makers you want, `country` for a local subsidiary of a global domain, `limit` (≤ 25)
- apollo's search hides last names: reveal someone with `apollo_match_person(person_id=<apollo_id>)`, never by name + company
- no linkedin page yet? `waterfall_company` finds it (from the website, then by name)
- then, per person: `waterfall_email` and `waterfall_phone`
