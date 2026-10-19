## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — kaspr, forager, lusha, lemlist
- with the default order, providers you hold your **own** key for (and the free steps) run first, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_phone` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `kaspr,lusha`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a person's phone number

one person per call, stopping at the first provider that returns a number.
- `waterfall_phone` — default order: kaspr → forager → lusha → lemlist; the linkedin url opens the most steps
- phone credits cost more than email credits: narrow the steps to the provider you want to spend on
- lemlist answers asynchronously (see `pending` in the response)
- `dry_run=true` shows the plan without spending anything
