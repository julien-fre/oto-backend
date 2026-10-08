## prerequisite — authorize Google Tasks on your Google account

from this card, click **connect**: Google asks you to authorize **Google Tasks only** (scope `tasks`) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- several Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` says which ones have authorized Google Tasks
- an account that has not authorized Google Tasks is refused by the tools, naming this card — come back here to authorize it

## usage — task lists

`tasks_lists` and `tasks_task(op=list|get|create|update|complete|delete)` — under the chosen account.
- "add a task `follow up X` for Monday"
- "which tasks are overdue?"
