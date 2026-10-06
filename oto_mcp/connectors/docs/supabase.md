## prerequisite — supabase api key (pat)

create a personal access token (`sbp_…`) in [Supabase](https://supabase.com) (Account → Access Tokens), then paste it into oto.
- it is a **Management API** token (not a project key)

## usage — management api: projects, auth, logs

drive your Supabase projects via the Management API: list, auth config, log queries.
- "list my Supabase projects"
- "show the auth config of project `doeb…` (site_url, redirect allow-list, providers)"
- "pull the latest `auth_logs` of the project"
- "query the `postgres_logs` over the last 2 hours"
