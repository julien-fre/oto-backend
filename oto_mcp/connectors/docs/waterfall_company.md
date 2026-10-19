## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — the french registry (free), ai ark (`aiark`), apollo, theirstack, cognism
- with the default order, providers you hold your **own** key for (and the free steps) run first, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_company` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `fr,apollo,aiark,linkedin`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a company's firmographics

one company per call, stopping at the first source that identifies it.
- `waterfall_company` — default order: fr (the french registry: by siren, else by name) → aiark (domain or name) → apollo (domain) → theirstack (domain) → cognism; then, only when the company's linkedin page is still missing, linkedin (its own website, else a search by name checked against the domain)
- a search result only counts if its domain matches, or its name contains the name asked — a namesake never stops the cascade
- `employees_band` (french registry) is the insee size band code, not a headcount
- `dry_run=true` shows the plan without spending anything

## usage — qualify before you spend

- `qualify=` takes a Jev rubric (same shape as `jev_ask`'s `questions`): the company found gets typed answers with probabilities, in ONE call for micro-dollars, instead of being read and judged by the agent
- `qualify_min=0.7` keeps only what passes every yes/no question (the record stays, with `qualification.passed`)
- only the fields a fit needs are sent to Jev (title, seniority, industry, size…), never a name, email, phone or profile URL
- no Jev key or no time left: results come back unqualified and the receipt's `qualification` says why
