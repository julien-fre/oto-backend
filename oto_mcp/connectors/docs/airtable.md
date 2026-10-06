## prerequisite — airtable personal access token

create a **personal access token** at [airtable.com/create/tokens](https://airtable.com/create/tokens), then paste it into oto (it starts with `pat…`).
- two settings, NOT just one — this is airtable's #1 trap:
  1. **scopes**: `data.records:read` + `data.records:write`, `data.recordComments:read` + `data.recordComments:write`, `schema.bases:read` + `schema.bases:write`
  2. **access**: explicitly add each base (or the whole workspace) that the token must see
- a token with all the scopes but **no base granted** answers `200` with an empty list, never an error: oto's "test connection" button detects it and says so
- byo-only: no shared oto key — an airtable token is tied to an account and to specific bases

## usage — read and write in a base

- "which bases can I reach?" → `airtable_base()` (returns the `appXXXXXXXX` — the starting point for everything else)
- "what is in this base?" → `airtable_table(base_id="app…")` (tables, fields, types, options, views)
- "which columns exactly, before writing?" → `airtable_field(base_id="app…", table_id="tbl…")`
- "the rows where the status is Done" → `airtable_record(base_id="app…", table="tbl…", filter_by_formula="{Status}='Done'")`
- "add these 40 prospects" → `airtable_record(op="create", records=[{"Name": "…", "Email": "…"}, …])` (split into batches of 10 automatically)
- "update if the email exists, create otherwise" → `airtable_record(op="upsert", records=[…], merge_on=["Email"])`
- "fix this row" → `airtable_record(op="update", record_id="rec…", fields={"Status": "Signed"})`
- "comment on this row" → `airtable_comment(op="create", base_id=…, table=…, record_id=…, text="follow-up sent")`
- "attach this PDF" → `airtable_attachment(base_id=…, record_id=…, field="Attachments", filename="quote.pdf", content_type="application/pdf", file_base64=…)`
- "create a tracking table" → `airtable_table(op="create", name="Tracking", fields=[{"name": "Name", "type": "singleLineText"}, {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": "To do"}, {"name": "Done"}]}}])`

## note — names vs identifiers, typecast, batches

- **use identifiers, not names**: `tbl…`, `fld…` are stable; a table or column name changes as soon as someone renames it in the interface, and the automation silently breaks. `airtable_table` and `airtable_field` return the identifiers.
- **`typecast` is disabled by default, on purpose**: at airtable it is not a convenience conversion but a **schema change triggered by a data write** — it creates the missing option of a select, even a record in the linked table of a *linked record* field. writing "Signed" into a select that only knows "Signee" therefore fails outright instead of adding a duplicate option. pass `typecast=True` when you WANT this behavior.
- **`replace=True` on an update is destructive**: it is a PUT, every column not sent is CLEARED. the default (PATCH) only touches the columns sent.
- **batches**: airtable refuses more than 10 rows per request and 5 requests/second per base. oto splits and spaces out automatically, up to 200 rows per call. if airtable cuts off because of the rate, the response carries `aborted: "rate_limit"` and the number of rows actually written — retry 30 seconds later with the rest.
- **long lists**: `airtable_record` follows pagination up to `max_records` (100 by default) and sets `more: true` + `offset` if rows remain. nothing is silently truncated.
- **attachments > 5 MB**: host the file and write its URL into the column — `airtable_record(op="update", fields={"Attachments": [{"url": "https://…"}]})`. airtable fetches it itself.
- **`airtable_sync` is not a CSV import**: it feeds a table created in airtable via "Sync from other sources → API", and each push REPLACES the synced content. to load rows into an ordinary table, use `airtable_record(op="create")`.
- **nothing can be deleted in the schema, and this is verified**: no field `DELETE` (404), no table deletion, and a field `PATCH` carrying `options` is refused (`422` "Changing a field's type or number precision is not currently supported"). in practice: a select option created by mistake with `typecast=True` can ONLY be removed in the Airtable interface. one more reason to leave `typecast` false.
- **deleting a base is impossible through the API**: `airtable_base(op="create")` has no counterpart, it is done in the interface.
