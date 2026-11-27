## prerequisite — a snowflake programmatic access token and your account identifier

in Snowsight, **your user menu → Settings → Authentication → Programmatic access tokens → Generate new token**, then paste it into oto with **your account identifier** in the org-account form (`myorg-myaccount`, as in `https://myorg-myaccount.snowflakecomputing.com`). Reference: [Snowflake SQL API](https://docs.snowflake.com/en/developer-guide/sql-api/intro).
- ⚠️ **restrict the token to a read-only role** (`ROLE_RESTRICTION`): USAGE on one warehouse, SELECT on what the agent may read. Snowflake has no read-only token: the role is the boundary
- ⚠️ the token's user must be under a **network policy that allows oto**, or Snowflake refuses the token (401); an account admin sets it
- a legacy locator with a region (`xy12345.eu-west-1.aws`) is not accepted: use the org-account form
- your own token only: no shared oto key — the data is your own
- the "test connection" button reads the session (user, role, default warehouse): no table is scanned

## usage — explore, then query

one tool, `snowflake`, read only; each call names its function in `op`.

- "whose token, which warehouse" → `snowflake(op="get_session")` — an empty warehouse means every query must name one
- "what data is there" → `snowflake(op="run_query", statement="SHOW DATABASES")`, then `SHOW SCHEMAS IN DATABASE X`, `SHOW TABLES IN SCHEMA X.Y`, `DESCRIBE TABLE X.Y.Z`
- "top customers in France" → `snowflake(op="run_query", statement="SELECT name, SUM(amount) FROM X.Y.ORDERS WHERE country = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 20", bindings={"1": {"type": "TEXT", "value": "FR"}}, warehouse="COMPUTE_WH")`
- "a heavy query" → add `async=true`: the answer comes at once with a `statementHandle` (a call never waits past 45 s, and a cut call loses the handle while the query keeps billing)
- "is it done" (answer code `333334`) → `snowflake(op="get_statement", statement_handle="…")`, again after a few seconds until rows come
- "the rest of the result" (several entries in `resultSetMetaData.partitionInfo`) → `snowflake(op="get_statement", statement_handle="…", partition=1)`

## note — cost and limits

- ⚠️ **every query bills your warehouse credits** while it runs (a suspended warehouse resumes): always `LIMIT`, filter, select only the columns needed
- only statements that open with SELECT, WITH, SHOW, DESCRIBE, DESC or EXPLAIN, never the flow operator `->>`; one statement per call; values in `bindings`, never pasted into the SQL
- a SQL error comes back as Snowflake's own message (code, sqlState): read it, fix the SQL
- unquoted identifiers are upper case; `warehouse`, `role`, `database`, `schema` omitted = the user's defaults
- every query carries `query_tag = oto` in your query history
- not covered: writes (INSERT, UPDATE, MERGE, DDL, GRANT, CALL), cancelling a query, file PUT/GET
