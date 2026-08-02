## prerequisite — ta clé api n8n + url d'instance

n8n s'auto-héberge ou tourne en cloud, donc deux champs sont attendus.
- `api_key` — depuis ton instance, ouvre settings puis n8n API et crée une clé api
- `base_url` — l'url de ton instance (ex. `https://ton-instance.app.n8n.cloud` ou ton url self-hosted)
renseigne les deux dans tes clés de connecteur oto sous `n8n`. plus d'infos sur [n8n.io](https://n8n.io)

## usage — piloter workflows et exécutions

liste, active et inspecte tes workflows et leurs runs.
- `n8n_list_workflows` liste les workflows (filtre `active`, `tags`), `n8n_get_workflow` détaille un workflow
- `n8n_activate_workflow` / `n8n_deactivate_workflow` démarrent ou stoppent ses triggers/cron
- `n8n_list_executions` les exécutions (filtre par workflow ou `status` success/error/waiting)
- `n8n_get_execution` le détail d'une exécution (avec `include_data` pour les données par nœud)
