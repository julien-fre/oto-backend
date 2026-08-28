## prerequisite — ta clé api phantombuster

- depuis [phantombuster.com](https://phantombuster.com), ouvre les paramètres de ton organisation puis la section api key
- copie ta clé api
- colle-la dans tes clés de connecteur oto sous `phantombuster`

## usage — lancer des agents et récupérer leurs résultats

déclenche un agent (phantom) puis suis son run et récupère ses résultats.
- `phantombuster_get_agent` la configuration et le statut d'un agent
- `phantombuster_launch_agent` démarre un run (⚠️ consomme des crédits et agit sur des comptes tiers), renvoie le `containerId`
- `phantombuster_list_containers` / `phantombuster_get_container` listent et suivent les runs
- `phantombuster_container_results` récupère les résultats json d'un run terminé, `phantombuster_container_output` ses logs
