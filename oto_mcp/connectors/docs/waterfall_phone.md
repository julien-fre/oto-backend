## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — pipecorn, kaspr, lusha, apollo (phone reveal, your own key), forager, lemlist, dropcontact, fullenrich
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
- `waterfall_phone` — default order: pipecorn (mobiles only, from the linkedin url) → kaspr → lusha → forager → apollo (reveal, your own key) → lemlist → dropcontact → fullenrich; the linkedin url opens the most steps; an asynchronous step still `pending` ends the cascade
- phone credits cost more than email credits: narrow the steps to the provider you want to spend on
- apollo's reveal, lemlist, dropcontact and fullenrich answer asynchronously: each waits up to ~25 s, otherwise it is `pending` (see `pending` in the response) — fullenrich usually takes 30 s to 4 min
- `dry_run=true` shows the plan without spending anything
- for a whole list, `waterfall_phone_act(op="update_rows")` runs it over a table's rows on the server: only rows with an empty `status_column` are taken, rows with the same inputs share one lookup, the find is written into each row and the answer is counts only — call again until `remaining` is 0; the asynchronous steps are left out there
