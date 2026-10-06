## prerequisite — get a hunter.io key

create an api key in the api settings of your [hunter.io](https://hunter.io) account.
- paste it into your oto connectors on `/account`
- members can use the platform key (daily quota); a guest must set their own
- hunter bills in credits (1 credit per call, 1 per batch of 10 emails on domain search)

## usage — find and verify emails

discover a company's emails, guess a person's, and check its deliverability.
- `hunter_domain_search` — lists the public emails found on a domain + the address pattern
- `hunter_email_finder` — the email of a specific person at a company (name + domain)
- `hunter_email_verify` — checks that an address is deliverable
