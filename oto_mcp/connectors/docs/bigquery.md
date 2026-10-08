## prerequisite — authorize Google BigQuery on your Google account

from this card, click **connect**: Google asks you to authorize **Google BigQuery only** (scope `bigquery`) on the account you choose. the Google account itself (address, token) is carried by the **Google account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- queries see exactly what **your BigQuery rights** see: "BigQuery Data Viewer" role on the datasets to read, "BigQuery Job User" on the project that runs (and pays for) the queries
- **read-only**: every query is dry-run validated first, anything that is not a SELECT is refused before execution
- **bounded cost**: at most 10 GB read per query by default (can be raised up to 1 TB per query), refused with the estimate beyond that
- several Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` tells which ones have authorized Google BigQuery

## usage — explore and query the warehouse

`bigquery_catalog` (projects → datasets → tables), `bigquery_table` (schema + free preview), `bigquery_query` (SQL, `dry_run` to estimate), `bigquery_results` (resume a long query, next page).
- "which tables does the `analytics` dataset have?"
- "how many orders per month in 2026, from `dwh.sales.orders`?"
- "estimate the cost of this query before running it"
