## prerequisite — get a ZeroBounce key

create an API key in the API settings of your [zerobounce](https://www.zerobounce.net) account.
- paste it into your oto connectors on `/account` — zerobounce is **byo** (everyone brings their own key)
- the key consumes your account's verification credits

## usage — check email deliverability

validates one or more email addresses before sending (status valid, invalid, catch-all, spamtrap…).
- `zerobounce_verify_email` — verifies one address
- `zerobounce_verify_batch` — up to 200 addresses in one call
- `zerobounce_credits` — remaining verification credits
