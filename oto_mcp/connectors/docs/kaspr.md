## prerequisite — get a kaspr key

create an api key in the api/integrations settings of your [kaspr](https://app.kaspr.io) account.
- paste it into your oto connectors at `/account` — kaspr is **byo** (no platform key, everyone brings their own)
- kaspr bills in credits: 1 per email, +1 per phone number
- check your key with `oto_instance(op='verify', connector='kaspr')`

## usage — enrich a contact from linkedin

get a person's emails and phone numbers from their linkedin profile.
- `kaspr_enrich_linkedin` — pass the slug (`alexis-laporte`) or the full linkedin url, `with_phone` option for phone numbers
