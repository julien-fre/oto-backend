## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — dataforseo, apify (`website`), theirstack
- with the default order, providers you hold your **own** key for run first, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_tech` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `theirstack,dataforseo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a company's tech stack

one company per call, stopping at the first source that returns a stack.
- `waterfall_tech` — default order: dataforseo (domain — what the website runs, from dataforseo's index, ~$0.01-0.02) → website (domain — a live crawl of the site, ~$0.01 of apify) → theirstack (domain or name — what the job postings name: crm, erp, data stack, internal tools; ~3 theirstack credits)
- the website sources see the front (cms, framework, hosting, analytics, e-commerce); theirstack sees the back office — `source` on the result says which (`website` | `job_postings`)
- `technologies=["Shopify", "HubSpot"]` asks « does it use one of these? »: a source that detects none of them does not stop the cascade, and `matched` lists the ones found; `found: false` means no source detected them, not that the company does not use them
- `providers=["theirstack"]` reads the back office first
- `dry_run=true` shows the plan without spending anything
- for a whole list, `waterfall_tech_act(op="update_rows")` runs it over a table's rows on the server: only rows with an empty `status_column` are taken, rows with the same inputs share one lookup, the find is written into each row and the answer is counts only — call again until `remaining` is 0
