## prerequisite — hithorizons api key

**byo** connector: everyone connects their own hithorizons account.
- create an account on [hithorizons](https://www.hithorizons.com) and subscribe to the api (azure api management)
- get your subscription key (`Ocp-Apim-Subscription-Key`)
- set it on your oto dashboard (`hithorizons` connector)

## usage — european company data

europe-wide company search and profiles (default country FR, overridable).
- `hithorizons_search_company(name=…, city=…, country=…)` — search by name + city/postal code
- `hithorizons_suggestions(query=…)` — name autocomplete
- `hithorizons_company(company_id)` — full profile from a hithorizons id
