## prerequisite — connect the providers it cascades over

the waterfall has no key of its own: each step calls another connector's tool with that connector's key.
- connect (and have activated for your org) the providers you want in the cascade — hunter, apollo, ai ark, kaspr, forager, lusha for people; apollo for companies (the french registry steps are free)
- a provider that is not connected or not activated is simply skipped, and the receipt says so
- in a hosted agent, list the step tools you want it to use (e.g. `hunter_email_finder`, `apollo_match_person`) next to `waterfall` — a step outside the job's tool list is skipped
- each step is billed by its provider exactly as a direct call would be — the waterfall adds no cost of its own

## usage — email, then phone, then company

one contact (or one company) per call, stopping at the first provider that finds the value.
- `waterfall` `op=email` — a person's professional email: hunter → apollo → ai ark → kaspr → forager → lusha
- `op=phone` — a person's phone number: kaspr → ai ark → forager → lusha (the linkedin url opens the most steps)
- `op=company` — a company's firmographics: french registry by siren → french registry by name → apollo by domain
- `providers=[…]` reorders or narrows the steps; `dry_run=true` shows the plan without spending anything
- every response carries `attempts`: what each provider did (found, no match, skipped, failed — with the reason)
- for a whole list, run it from a recipe (`oto_recipe`, `for_each` over the rows of a table)
