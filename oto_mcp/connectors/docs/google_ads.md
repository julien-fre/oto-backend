## prerequisite — authorize Google Ads on your Google account

from this card, click **connect**: Google asks you to authorize **Google Ads only** (scope `adwords`) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- pick the Google account that has access to your Google Ads accounts (often the one of your manager account / MCC): the tools see exactly the Google Ads accounts **that Google user** can open
- **read-only**: the tools only read (GAQL queries, accessible accounts, field lookup) — nothing is created, edited, paused or spent
- **no developer token to provide**: Google retired it on 2026-09-09; the API access level is the one of the platform's OAuth app — a refusal saying "approved for test accounts only" is fixed by the platform's administrator, not by reconnecting
- access through a manager account (MCC): pass its id as `login_customer_id` to read its client accounts
- several Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` tells which ones have authorized Google Ads

## usage — read campaigns, ads and their performance

`google_ads_customers` (accessible accounts), `google_ads_fields` (what a resource offers), `google_ads_search` (one GAQL query, next page by `page_token`).
- "which campaigns spent the most over the last 30 days, with clicks and conversions?"
- "daily spend of the account since the start of the month"
- "which search terms cost money without converting last week?"
