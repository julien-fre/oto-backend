## prerequisite — authorise Google Calendar on your Google account

from this card, click **connect**: Google asks you to authorise **Google Calendar only** (`calendar` scope) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorise several services, one service at a time, without reauthorising the others.
- several Google accounts: each tool acts on the default account, or on the one you target with `account=<email>`; `google_accounts` says which ones have authorised Google Calendar
- an account that has not authorised Google Calendar is refused by the tools, naming this card — come back here to authorise it

## usage — calendar

`calendar_calendars` and `calendar_event(op=list|get|create|update|delete)` — under the chosen account.
- "what do I have on my calendar tomorrow?"
- "create a follow-up slot on Friday at 10am with [contact]"
- "move Monday's meeting to 3pm"
