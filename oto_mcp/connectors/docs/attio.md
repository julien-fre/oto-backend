## prerequisite — your attio api key

attio exposes one api key per workspace. go to your [attio workspace's developer settings](https://app.attio.com), **api** section, and create a key (access token).
- paste it into oto on your account (`/account`), **attio** connector
- no shared platform key: everyone sets their own
- remember to tick the records + notes + tasks + lists permissions depending on what you want to do; to touch the schema (attributes, options, deal stages), the `object_configuration:read-write` permission (`list_configuration:read-write` for a list)
- note: the official attio mcp connector is often preferred; oto keeps the code for custom implementations

## usage — what you can do

drive your attio crm (companies, people, deals) + notes, tasks, lists and comments from claude.
- "look up the company acme" → `attio_record(op="search", object="companies")`, then `attio_record(op="get", object="companies")` for the details
- "create a contact jean dupont at acme" → `attio_record(op="create", object="people")`
- "add a note on this deal" → `attio_note(op="create")` (title + markdown, attached to the record)
- "list my open tasks" → `attio_task(op="list")`, and `attio_task(op="create")` to add one
- "add the negotiation stage to my deals" → `attio_attribute(op="statuses", target="objects", identifier="deals", attribute="stage")` to re-read what exists, then `attio_attribute(op="create_status", title=…)`; same gesture for a select option (`op="create_option"`) or a new attribute (`op="create"`, `definition`). ⚠️ writes the crm schema, and the attio api cannot undo it
