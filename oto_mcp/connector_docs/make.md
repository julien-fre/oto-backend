## prerequisite — ton token api make + url de zone

make est régionalisé (eu1/us1/eu2…), donc deux champs sont attendus.
- `api_token` — depuis [make.com](https://www.make.com), ouvre profile puis API/SDK et génère un token
- `base_url` — l'url de ta zone make (ex. `https://eu1.make.com`)
renseigne les deux dans tes clés de connecteur oto sous `make`

## usage — lister et exécuter tes scénarios

un workflow make = un **scénario**, qui appartient à une équipe d'une organisation.
- `make_list_organizations` puis `make_list_teams` pour découvrir les ids, `make_list_scenarios` les scénarios d'une équipe
- `make_get_scenario` les métadonnées d'un scénario, `make_get_scenario_blueprint` la structure de ses modules
- `make_run_scenario` déclenche un run (avec un `data` d'entrée optionnel)
- `make_list_scenario_logs` les logs d'exécution d'un scénario
