## prerequisite — connect the providers it cascades over

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — hunter, apollo, ai ark (`aiark`), kaspr, forager, lusha, lemlist
- providers you hold your **own** key for always run first; the shared platform keys next; a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_email` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `lemlist,hunter,apollo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a person's professional email

one person per call, stopping at the first provider that returns an email.
- `waterfall_email` — default order: hunter (name + domain) → apollo → aiark → kaspr → forager → lusha → lemlist
- lemlist answers asynchronously: the step waits up to ~40 s, otherwise it is `pending` and the response gives the `lemlist_enrich_result` call to make later
- the email is not verified here — chain `zerobounce_verify_email` or `hunter_email_verify` if you need it
- `dry_run=true` shows the plan (order, key per step, arguments) without spending anything
- for a whole list, run it from a recipe (`oto_recipe`, `for_each` over the rows of a table)
