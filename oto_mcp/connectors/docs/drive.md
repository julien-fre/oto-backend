## prerequisite — authorize Google Drive on your Google account

from this card, click **connect**: Google asks you to authorize **Google Drive only** (scope `drive`) on the account you choose. the Google account itself (address, token) is carried by the **Google Account** connector — one account can authorize several services, one service at a time, without reauthorizing the others.
- several Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` tells which ones have authorized Google Drive
- an account that has not authorized Google Drive is refused by the tools, naming this card — come back here to authorize it

## usage — list, read, organize, share

`drive_file(op=list|get|download|move|delete…)` and `drive_access` — under the chosen account.
- "list the files modified this week in the `clients` folder"
- "read this `.xlsx` from my Drive, `quotes` tab" — a spreadsheet comes back as CSV per sheet, bounded; `sheet` picks the tab, `max_rows` the bound
- "share this folder read-only with jane@…"
