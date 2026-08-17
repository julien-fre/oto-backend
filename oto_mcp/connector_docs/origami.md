## prerequisite — clé api origami

crée une clé API dans Origami (Settings → API keys ; elle commence par `og_live_` — voir la [doc d'authentification](https://docs.origami.chat/authentication)), puis colle-la dans oto.
- byo-only : les crédits d'enrichissement et les envois sont ceux du compte Origami de l'org
- pour envoyer, un compte email et/ou LinkedIn doit être connecté dans Origami — sinon le lancement répond `blocked.missingChannels` et rien ne part

## usage — des leads au lancement d'une campagne email + LinkedIn

origami tient des tables de leads et fait rédiger puis envoyer des campagnes multicanal par son agent.
- « quels workspaces / quelles tables ? » → `origami_workspaces`, `origami_tables(op="list")`
- « crée une table à partir de ce CSV » → `origami_upload_csv(workspace_id, "leads.csv", csv_text)` (`dry_run=True` montre les premières lignes)
- « ajoute / mets à jour ces contacts dans la table » → `origami_tables(op="columns")` pour lire les SLUGS, puis `origami_rows(op="upsert", rows=[{slug: valeur}], match_columns=["email"])`
- « lis les lignes » → `origami_rows(op="list", max_pages=…)` (suit `nextCursor` côté serveur)
- « rédige une campagne sur cette table » → `origami_campaign_create(table_id, instructions)` puis `origami_run_get(agent_id, run_id)` jusqu'à `status != "running"`
- « lance-la » → `origami_campaign_launch(campaign_id, dry_run=False)` — le défaut `dry_run=True` ne fait qu'un aperçu
- « où en est-elle ? » → `origami_campaigns(op="stats" | "people")`, `origami_sequences(workspace_id=…)`
- « pause / reprise / suppression » → `origami_campaign_pause`, `origami_campaign_resume`, `origami_campaign_delete(confirm=True)`

## note — ce qui envoie, ce qui coûte, ce qui piège

- **lancer envoie pour de vrai** (emails + messages LinkedIn à des personnes réelles) : relire les personnes enrôlées et le texte AVANT `dry_run=False` ; il n'y a pas de rappel
- `origami_campaign_create` avec `block_prior_contacts=True` (défaut) écarte toute personne déjà enrôlée auparavant, MÊME dans un brouillon supprimé jamais envoyé — passer False seulement si ces enrôlements n'ont jamais envoyé à personne
- les clés de lignes sont les **slugs** des colonnes d'entrée (avec des tirets), pas les noms affichés — un slug inconnu est refusé (400 UNKNOWN_FIELDS)
- `enrich=False` par défaut à l'upsert : l'enrichissement dépense des crédits, il se demande explicitement
- la suppression est en deux temps ; le tool re-lit la campagne et ne dit « supprimée » que sur un 404
- il n'y a pas de liste globale des campagnes : lister par table, ou passer par les séquences d'un workspace
