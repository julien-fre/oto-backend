## prerequisite — get a Lusha key

create an API key in the [Lusha](https://www.lusha.com) dashboard, API settings section.
- paste it into your oto connectors on `/account`
- byo-only: no shared platform key, each org/person sets their own
- Lusha bills in credits: a search alone (`api_search`) costs one credit, and each revealed field (`reveal`) costs one more PER contact — watch `billing.creditsCharged` in the response before revealing a big batch.

## usage — find and reveal contacts

search for contacts and unlock their emails/phones in a single call.
- `lusha_search_and_enrich` — up to 100 contacts per call, identified by email, LinkedIn URL, or name + company. `reveal` controls what gets unlocked (emails, phones, or both); without `reveal`, the call only searches/matches, without unlocking any data.
