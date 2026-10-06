## prerequisite — authorize Google Sheets on your Google account

from this card, click **connect**: Google asks you to authorize **Google Sheets only** (`spreadsheets` scope) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- several Google accounts: each tool acts on the default account, or on the one you target with `account=<email>`; `google_accounts` says which ones have authorized Google Sheets
- an account that has not authorized Google Sheets is refused by the tools, naming this card — come back here to authorize it

## usage — read and write a spreadsheet

`sheets_spreadsheet(op=describe|read|write|clear)` and `sheets_create` — under the chosen account.
- "read the `leads` tab of this sheet"
- "write these rows after the end of the `suivi` tab"
- "create a spreadsheet `Prospects octobre` and fill it in"
