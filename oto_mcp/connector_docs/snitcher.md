## prerequisite — personal access token snitcher

génère un Personal Access Token dans Snitcher (app → Settings → Account → API → Generate New Token — voir la [doc REST API](https://docs.snitcher.com/product/rest-api/introduction)), puis colle-le dans oto.
- byo-only : pas de clé oto partagée — un PAT est lié à UN compte Snitcher
- limite de débit : 60 requêtes/minute par jeton (429 au-delà)

## usage — quelles entreprises visitent votre site

snitcher identifie les ENTREPRISES derrière le trafic anonyme de votre site, en 5 tools :
- « quels workspaces ai-je ? » → `snitcher_workspace(op="list")` — **toujours commencer là** : le `workspace_uuid` rendu est requis par tous les autres tools
- « quelles entreprises ont visité le site cette semaine ? » → `snitcher_organisation(workspace_uuid="...", op="list", date_from="2026-08-17")`
- « les entreprises revues ces 30 derniers jours avec plus de 5 pages vues » → `snitcher_organisation(op="search", filters={"operator": "AND", "conditions": [{"field": "last_seen", "comparison": "less_than_x_units_ago", "value": 30, "unit": "day"}, {"field": "pageviews", "comparison": "greater_than", "value": 5}]})` — ⚠️ conditions À PLAT seulement (les groupes imbriqués du spec sont refusés en vrai, 422), et champs limités au comportement de visite : last_seen, first_seen, tag, sessions, pageviews, time_on_site, url, referrer, source — PAS les firmographiques (name/industry/size → passer par `op="list", name="..."` ou un segment)
- « que fait cette entreprise sur le site ? » → `snitcher_session(workspace_uuid="...", organisation_uuid="...")` — chaque session porte un tableau `events` : pageviews (avec time_on_page), soumissions de formulaires (AVEC les valeurs des champs), événements custom `track`, clics, téléchargements
- « toutes les sessions d'hier » → `snitcher_session(workspace_uuid="...", date="2026-08-22")` — sans `organisation_uuid`, `date` ou `date_from` est requis
- « qui sont les décideurs chez cette entreprise ? » → `snitcher_contact(op="list", organisation_uuid="..." | domain="acme.com")`
- « révèle l'email de ce contact » → `snitcher_contact(op="reveal_email", contact_uuid="...")` — ⚠️ **dépense un crédit Snitcher**, confirmer l'intention avant
- « tague cette entreprise "hot lead" » → `snitcher_workspace(op="create_tag", tag_name="hot lead")` puis `snitcher_organisation(op="tag", organisation_uuid="...", tag_name="hot lead")`
- « quels segments existent ? » → `snitcher_workspace(op="segments")` — leurs uuids filtrent organisations et sessions
- « note le tier de ce compte » → `snitcher_custom_field(op="set", organisation_uuid="...", key="account_tier", value="enterprise")` — `op="set_many"` pose jusqu'à 50 champs d'un coup, les clés inconnues sont créées automatiquement (type inféré)

## ⚠️ notes

- `snitcher_contact(op="reveal_email")` est le SEUL appel payant (crédits) — tout le reste est lecture ou écriture gratuite (tags, custom fields, admin workspace)
- `snitcher_workspace(op="delete")` détruit le workspace ET son historique de visites — irréversible, à confirmer explicitement avec l'utilisateur
- `date` (un jour) et `date_from`/`date_to` (une plage) sont mutuellement exclusifs partout où les deux existent
- `visible_in_spotter=true` sur un custom field expose ses valeurs à tout script du site suivi (réponse Spotter) — off par défaut, à laisser off sauf besoin explicite
- vider un multi-select ne passe PAS par `op="set"` avec une liste vide (refusé par l'API) — utiliser `op="clear"`
- **testé en live le 2026-08-24** avec un vrai token trial (workspace tulina.ai) : 24 des 27 endpoints exercés — toutes les lectures, le cycle tag complet (create → attach → vérifié sur l'organisation → detach), le cycle custom-field complet (définitions + valeurs, nettoyé derrière) ; non exercés : reveal_email (crédit), create/delete workspace, invite
- la forme des réponses VARIE par endpoint (confirmé en live) : les listes portent la pagination Laravel au niveau racine (`success`/`current_page`/`total`/`data`), les gets rendent l'objet nu sans enveloppe, les tags rendent `{success, message}`, les DELETE rendent un corps vide — ⚠️ ne JAMAIS supposer `result["data"]` partout : `get_organisation` par exemple rend l'objet nu, `result["data"]` y lève une KeyError
- **le piège le plus fin est DANS `snitcher_custom_field`** : `op="set"` (un champ, PUT) rend l'objet valeur NU, mais `op="set_many"` (plusieurs champs, PATCH) rend `{"success", "data": [...]}` — même intention (« poser une valeur »), enveloppe différente selon le verbe. Chaque `op=` a sa forme documentée dans la description du tool, à relire avant de parser le retour plutôt que de deviner
- `snitcher_custom_field(op="set_many")` crée bien les clés inconnues automatiquement, type inféré (confirmé : un `42` a créé un champ `number`) ; `op="values"` rend aussi les champs SYSTÈME fixes (name, website, description…, `source: "fixed"`) à côté des customs
- `snitcher_contact(op="list", domain="...")` marche pour n'importe quelle entreprise, pas seulement les visiteurs identifiés (confirmé : 25 contacts sur un domaine tiers) — les emails restent `"[not-revealed]"` tant que le reveal payant n'a pas été fait
