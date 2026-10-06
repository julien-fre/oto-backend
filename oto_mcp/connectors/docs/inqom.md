## prerequisite — your inqom api access

the inqom api opens with two pairs: the application keys and an inqom account on whose behalf oto acts. this account bounds what is visible.
- `client_id` and `client_secret` — the api application keys, provided by inqom (ask your inqom contact for them)
- `username` and `password` — the inqom account used; inqom recommends a non-personal system account of the firm, which sees all its files
fill in these four fields in your oto connector keys under `inqom`

## usage — read the accounting of a file

- `inqom_company` the accessible firms/SMEs, then `inqom_dossier(op="list", company_id=…)` their files, `inqom_dossier(op="get")` a file's record
- `inqom_ref(kind="accounts")` the chart of accounts (third parties: `number_prefix="401"` or `"411"`), `kind="journals"` the journals, `kind="periods"` the fiscal years
- `inqom_balance` the balance over a period (the per-third-party detail is cut by default, `fields=["*"]` returns it)
- `inqom_entry_line(op="count")` then `op="list"` page by page (1,000 lines max, `page_number` starts at 1)
- several accounts at once: `account_prefixes=["6", "7"]` (an income statement), `["401"]` (suppliers) — lines sorted by date, each with `third_party_accounts`, the third-party accounts (40x/41x) of its entry: that is where the supplier of an expense is. reads the whole period: a month or a quarter
- `inqom_document` the download url of a document attached to a line
- the connector **never writes** to inqom: `inqom_entry_create` returns the named refusal `inqom_write_not_wired`, which describes the entries it would have created — nothing is sent; entry is done in inqom itself
