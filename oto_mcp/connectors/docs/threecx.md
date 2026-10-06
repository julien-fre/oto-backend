## prerequisite — your 3cx credentials

the connector reads your 3cx phone system with its address and one of the two credentials below, chosen by `auth_mode`. the rights of that credential bound what is visible.
- `base_url` — the https address of the 3cx web client, the one in the address bar when you open 3cx in your browser (e.g. `https://your-company.3cx.fr`)
- `auth_mode=user`: `username` and `password` — a 3cx account that sees the calls and recordings of the desired groups (a group manager is enough); two-factor authentication must be disabled on it; a dedicated account is better than a personal one
- `auth_mode=api_client`: `client_id` and `client_secret` — an api client created in the 3cx admin console (integrations > api), if your license allows it; system admin role to see the whole phone system
fill in `base_url`, `auth_mode` and the matching pair in your oto connector keys under `3cx`

## usage — call traces and recordings

- `threecx_call(date_from=…, date_to=…)` one page of the call log; `recorded_only=true` keeps the recorded segments; continue with `skip=next_skip` as long as `next_skip` is not null
- `threecx_call(op="export", date_from=…, date_to=…)` the whole period as a csv file (`;` separator, durations in seconds): the call traces of a day or a week, instead of a daily export
- `threecx_recording(rec_id=…)` the audio of a recording, using the `SrcRecId` or `DstRecId` of a call log row; returned as a short-lived signed link
- the connector **never writes** to 3cx
