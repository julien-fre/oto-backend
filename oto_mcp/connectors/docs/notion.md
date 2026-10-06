## prerequisite — your notion integration token

notion is accessed through an **internal integration**. create it at [notion.so/my-integrations](https://www.notion.so/my-integrations) and get the **internal integration token**.
- **share the pages/databases you want with your integration** in notion (`...` menu → connections) — otherwise it sees nothing
- for **comments**, tick "read comments" and "insert comments" in the integration's capabilities (unticked by default)
- paste the token into oto on your account (`/account`), **notion** connector

## usage — what you can do

read and write notion pages, databases and blocks shared with your integration.
- "find the roadmap page" → `notion_search` — a zero can mean "nothing is shared with the integration": the response then carries a `warning` saying how to tell (retry with `query=""`). a response returns at most 100 objects: `has_more: true` → pass its `next_cursor` back as `cursor` for the next page
- "what changed yesterday in notion?" → `notion_search` with `query=""` and `edited_on="YYYY-MM-DD"` (UTC day): all objects edited that day, in one response
- "list the rows of this database where status = to do" → `notion_query_database` (with filter)
- "create a page under this project" → `notion_create_page`
- "add this paragraph to the page" → `notion_append_blocks` (`position="start"` to write at the top)
- "fix this sentence / rewrite the section" → `notion_get_markdown` then `notion_edit_markdown` (search-and-replace)
- "file this page under Archives" → `notion_move_page`
- "comment / reply to the comment" → `notion_get_comments`, `notion_add_comment`
- "add a Due date column to the database" → `notion_update_database`; "create a database" → `notion_create_database`
- "create a table view filtered on To do" → `notion_view`

a database has one or more **data sources** (notion API 2025-09-03): `search` returns them under the `data_source` object. the database tools accept the id of the database OR of its data source — never that of a **linked view** (a copy of a database placed in another page): use the id of the original database.
