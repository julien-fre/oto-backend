## prerequisite — your n8n api key + instance url

n8n is self-hosted or runs in the cloud, so two fields are expected.
- `api_key` — from your instance, open settings then n8n API and create an api key
- `base_url` — your instance's url (e.g. `https://your-instance.app.n8n.cloud` or your self-hosted url)
fill in both in your oto connector keys under `n8n`. more info at [n8n.io](https://n8n.io)

## usage — manage workflows and executions

list, activate and inspect your workflows and their runs.
- `n8n_list_workflows` lists workflows (filter `active`, `tags`), `n8n_get_workflow` details one workflow
- `n8n_activate_workflow` / `n8n_deactivate_workflow` start or stop its triggers/cron
- `n8n_list_executions` the executions (filter by workflow or `status` success/error/waiting)
- `n8n_get_execution` the detail of one execution (with `include_data` for per-node data)
