## prerequisite — authorise Google Calendar on your Google account

from this card, click **connect**: Google asks you to authorise **Google Calendar only** (`calendar` scope) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorise several services, one service at a time, without reauthorising the others.
- several Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` says which ones have authorised Google Calendar
- an account that has not authorised Google Calendar is refused by the tools, naming this card — come back here to authorise it

## usage — calendar

`calendar_calendars` and `calendar_event(op=list|get|create|update|delete)` — under the chosen account.
- "what do I have on my calendar tomorrow?"
- "create a follow-up slot on Friday at 10am with [contact]"
- "move Monday's meeting to 3pm"
