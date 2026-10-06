## prerequisite — your clay tables (and, optionally, your api key)

a single clay card, several named entries — each has a **type**:
- `table`: a clay table where oto writes rows. in clay, open the table → **+ add** → **monitor webhook**, copy the **cURL command** shown and paste it as is into the webhook field (the url and the auth token are picked up). the entry's **name** is the one the agent will use to target the table
- `api`: your clay public api key (clay → settings → account → api keys). only needed to run routines, search the clay database or read tables. the key is **personal**: it spends your clay credits
- clay has no api to create a table webhook: each table is added from the clay ui, once

## usage — writing rows into a clay table

- `clay_list_tables` lists the registered tables (name, level, rows already sent)
- `clay_push_rows(table, row=…)` sends one row; `rows=[…]` up to 50 rows per call, with a receipt `{total, succeeded, failed}`
- one row = one json object = one send (a json array makes only ONE row). the whole object lands in the table's **webhook** column; its keys are mapped to columns once, in clay. clay then runs the table's enrichments on each new row (the owner's clay credits)
- webhook protected by a token: with no token or a wrong one, clay answers 401 and the batch stops at the first refusal
- `dry_run=True` validates and shows what would be sent, without sending anything
- if the requested table does not exist, the refusal lists the known tables: ask the user to add the right one on the clay card rather than guessing one

## usage — routines, search and tables via the api

- `clay_account`: who owns the key, and the workspace's credit balance
- `clay_run_routine(routine_id, items)` runs a routine (clay function or workflow) on 1 to 100 `{id, inputs}` items. it is **asynchronous**: the call returns a `routine_run_id`, then `clay_get_run` until `status = complete` (a few seconds between calls). no endpoint lists routines: ask the user for the id (`function:t_…`)
- `clay_search` searches people/companies in the clay database: filters mode (`source_type` + `filters`, fields via `clay_search_fields`) or query mode (`query`, grammar via `clay_search_reference`). next page: `clay_search_next(search_id, mode)`
- `clay_tables_query` reads rows from existing tables — clay **enterprise plan** only

## note — clay's limits

- a table webhook accepts **50,000 sends in total**, even if rows are deleted. oto counts what it sends and warns as the limit approaches; beyond it, create a new webhook in the table and paste it back on the same entry (the counter restarts from zero)
- each call consumes the clay credits of the account concerned, like the same work done in the clay ui
- rate limit per workspace: a 429 refusal indicates the wait time (`retry_after`) — wait it out before retrying
