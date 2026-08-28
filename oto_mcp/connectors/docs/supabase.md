## prerequisite — clé api supabase (pat)

crée un personal access token (`sbp_…`) dans [Supabase](https://supabase.com) (Account → Access Tokens), puis colle-le dans oto.
- c'est un token **Management API** (pas une clé de projet)

## usage — management api : projets, auth, logs

pilote tes projets Supabase via la Management API : liste, config d'auth, requêtes de logs.
- « liste mes projets Supabase »
- « montre la config auth du projet `doeb…` (site_url, redirect allow-list, providers) »
- « sors les derniers `auth_logs` du projet »
- « requête les `postgres_logs` sur les 2 dernières heures »
