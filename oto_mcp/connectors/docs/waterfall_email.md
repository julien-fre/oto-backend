## prerequisite — connect the providers you want it to try

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — hunter, apollo, ai ark (`aiark`), kaspr, lusha, dropcontact, lemlist, fullenrich, forager
- with the default order, providers you hold your **own** key for (and the free steps) run first, the shared platform keys next; an order you write is kept as written
- a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_email` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `lemlist,hunter,apollo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a person's professional email

one person per call, stopping at the first provider that returns an email.
- `waterfall_email` — default order: hunter (name + domain) → aiark → apollo → kaspr → lusha → dropcontact → lemlist → fullenrich → forager (last: on tulina's key the dearest email lookup)
- dropcontact, lemlist and fullenrich answer asynchronously: each step waits up to ~25 s, otherwise it is `pending` and the response gives the collect call to make later (fullenrich usually takes 30 s to 4 min)
- the email is not verified here — chain `zerobounce_verify_email` or `hunter_email_verify` if you need it
- `dry_run=true` shows the plan (order, key per step, arguments) without spending anything
- for a whole list, `waterfall_email_act(op="update_rows")` runs it over a table's rows on the server: only rows with an empty `status_column` are taken, rows with the same inputs share one lookup, the find is written into each row and the answer is counts only — call again until `remaining` is 0; the asynchronous steps are left out there
