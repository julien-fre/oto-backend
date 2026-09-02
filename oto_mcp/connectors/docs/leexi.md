## prerequisite — API Key ID + Key Secret leexi

génère une paire de clés dans Leexi (Settings → Company Settings → API Keys → *add*), puis colle les **deux** valeurs dans oto : l'`API Key ID` et le `Key Secret`.
- ⚠️ **le secret n'est montré qu'UNE fois**, à la création — s'il est perdu, il faut recréer une paire
- compte **admin Leexi requis** pour créer une clé : ce n'est pas un réglage qu'un utilisateur ordinaire peut faire
- byo-only : pas de clé oto partagée — ce sont les appels enregistrés de ton entreprise, chaque organisation pose la sienne
- ⚠️ **une clé neuve ne porte que `read_calls`.** Tout le reste — `read_users`, `read_teams`, `read_meeting_events`, `write_calls`, `write_meeting_events`, et surtout `write_users` / `write_teams` **qui engagent tes licences facturées** — doit être coché explicitement par un admin. Le bouton « tester la connexion » se contente donc de `read_calls` : c'est le seul scope qu'une clé par défaut possède, et sonder ailleurs ferait passer une clé saine pour une clé morte
- une **portée d'accès aux appels** se règle aussi côté Leexi, à côté des scopes : toute l'entreprise (défaut), l'accès d'un utilisateur donné, ou des règles d'accès. Elle décide quels appels la clé voit

## usage — ce qui s'est dit au téléphone, et ce qu'on en a retenu

- « de quoi a-t-on parlé avec ce client ? » → `leexi_calls(op="search", customer_email_address=["…"])` puis `op="get"` sur l'uuid rendu : c'est `get` qui rend le **transcript** et les topics, `search` n'a que les métadonnées
- « le compte rendu de ce rendez-vous » → `leexi_notes(op="list", call_uuid="…")` — les notes sont les sorties des prompts Leexi, et c'est là que vit le résumé, plutôt que dans le transcript brut
- « les appels de la semaine » → `leexi_calls(op="search", date_filter="performed_at", date_from="…", date_to="…")`
- « les appels de tel commercial » → `leexi_calls(op="search", owner_uuid=["…"])`, l'uuid venant de `leexi_users(op="list")`
- « enregistre ce rendez-vous à venir » → `leexi_meetings(op="create", fields={…})`, puis `op="launch_bot"` pour y envoyer l'assistant
- importer un enregistrement existant → `leexi_calls(op="presign", extension="mp3")`, téléverser le fichier sur l'URL rendue, puis `leexi_calls(op="create", fields={… "recording_s3_key": "…"})`

## note — quatre choses qui trompent

- ⚠️ **une liste vide n'est pas forcément une erreur** : si la portée d'accès de la clé ne couvre aucun appel, `leexi_calls(op="search")` rend une liste vide, et c'est un réglage valide. De même, un **404** sur `op="get"` peut vouloir dire « hors de la portée de cette clé », pas « n'existe pas » — Leexi répond 404 exprès sur ce qu'une clé n'a pas le droit de voir
- ⚠️ **un appel tout juste créé n'a pas encore ses notes** : la création est asynchrone (quelques minutes), et les complétions de prompt — résumé, chapitrage — arrivent APRÈS. Relire plus tard plutôt que de conclure qu'elles manquent
- ⚠️ **`leexi_users(op="deactivate")` ne supprime rien** : les appels et l'historique restent, les sessions tombent, et la licence se libère. Pour réactiver, `op="update"` avec `{"active": true}` — ce qui reprend une licence facturée
- ⚠️ **une équipe qui porte encore des utilisateurs ou des appels ne se supprime pas** (422) : la désactiver avec `leexi_teams(op="update", fields={"active": false})`, ce que l'éditeur recommande

## note — les limites d'usage

50 requêtes/minute, et seulement **10/minute pour la création d'appel**. Un import en masse doit donc s'étaler ; le connecteur respecte le `Retry-After` de Leexi en lecture, mais ne rejoue jamais une écriture (l'API n'a pas de clé d'idempotence, et un rejeu créerait un doublon).
