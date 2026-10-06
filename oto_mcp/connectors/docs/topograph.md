## prerequisite — topograph api key

**byo** connector billed per request: everyone connects their own account (no platform key).
- create an account and generate your key on [topograph](https://www.topograph.co) ([api docs](https://docs.topograph.co))
- put it on your oto dashboard (connector `topograph`)

## usage — kyb from european registries

Normalized kyb data and documents from European public registries (FR, GB, DE…).
- `topograph_search(query=…, country=…)` — find a company by name or registration number
- `topograph_company(country=…, registration_number=…)` — normalized data, `mode="onboarding"` (fast) or `"verification"` (rigorous kyb)
