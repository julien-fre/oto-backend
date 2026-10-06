## prerequisite — your zapier ai actions api key

zapier exposes to agents a catalogue of **actions** that you explicitly authorize — not a zap-management api.
- go to [actions.zapier.com](https://actions.zapier.com), choose the actions to expose
- get the associated api key (`x-api-key` header); the set of exposed actions is attached to this key
- paste it into your oto connector keys under `zapier`

## usage — run your zapier actions in natural language

discover the authorized actions and run them with a natural-language directive.
- `zapier_list_actions` lists the actions exposed by your key (id, description, fields)
- `zapier_execute_action` runs an action via its `action_id` + `instructions` (zapier fills the fields left in "ai guess" mode)
- pass `preview_only=True` to see what would be done without running it
- `zapier_execution_log` gives you the detail of an execution
