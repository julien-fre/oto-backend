## prerequisite — connect the providers it cascades over

this connector has no key of its own: each step calls another connector's tool, with that connector's key.
- connect the providers you want in the cascade on their own cards, and have them activated for your org — your connected linkedin account (free), ai ark (`aiark`), apollo, forager, cognism, lusha, lemlist
- providers you hold your **own** key for always run first; the shared platform keys next; a provider with no usable key is skipped without being called
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own
- in a hosted agent, list the step tools you want it to use next to `waterfall_person` — a step outside the job's tool list is skipped

## setup — choose your steps (optional)

set **steps** on this card: the provider keys, comma-separated, in the order you want — e.g. `linkedin,aiark,apollo`.
- an org admin sets it for the whole org; any member can set their own, which wins for them
- empty = the default order; a key that is not a step of this waterfall is ignored (the response names it under `ignored_steps`)
- `providers=[…]` on a call overrides both, for that call only

## usage — a person's current role and company

one person per call, stopping at the first provider that returns a title or a company.
- `waterfall_person` — default order: linkedin (your own connected account, free) → aiark → apollo → forager → cognism → lusha → lemlist
- give the linkedin url when you have it: it opens every step; an email or name + domain opens apollo, cognism, lusha and lemlist
- lemlist answers asynchronously (see `pending` in the response)
- `dry_run=true` shows the plan without spending anything
