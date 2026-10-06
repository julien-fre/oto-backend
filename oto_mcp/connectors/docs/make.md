## prerequisite — your make api token + zone url

make is regionalized (eu1/us1/eu2…), so two fields are expected.
- `api_token` — from [make.com](https://www.make.com), open profile then API/SDK and generate a token
- `base_url` — the url of your make zone (e.g. `https://eu1.make.com`)
enter both in your oto connector keys under `make`

## usage — list and run your scenarios

a make workflow = a **scenario**, which belongs to a team in an organization.
- `make_list_organizations` then `make_list_teams` to discover the ids, `make_list_scenarios` for a team's scenarios
- `make_get_scenario` for a scenario's metadata, `make_get_scenario_blueprint` for the structure of its modules
- `make_run_scenario` triggers a run (with an optional input `data`)
- `make_list_scenario_logs` for a scenario's execution logs
