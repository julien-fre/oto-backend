## prerequisite — clé api fireflies

crée une clé API dans Fireflies (app.fireflies.ai → Integrations → API — voir la [doc d'intégration](https://docs.fireflies.ai)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée
- limites par palier : Free 50 requêtes/jour, Pro 500/jour, Business/Enterprise 60/min — certaines mutations ont leur propre limite plus stricte (ex. suppression 10/min, partage 10/heure)

## usage — transcripts, réunion en direct, AskFred, org

fireflies transcrit tes réunions et donne accès aux transcripts, au contrôle d'une réunion EN COURS, à son assistant Q&A AskFred et aux données d'organisation, en 7 tools :
- « retrouve mes réunions sur le budget de la semaine dernière » → `fireflies_transcript(op="list", keyword="budget", mine=True)`
- « le résumé complet et les action items de cette réunion » → `fireflies_transcript(op="get", transcript_id="...")` (résumé, sentences, analytics, participants — tout en un appel)
- « transcris ce fichier audio » → `fireflies_transcript(op="upload", url="https://...", title="...")` — l'URL doit être publiquement accessible
- « renomme / rends privée / range dans un canal cette réunion » → `fireflies_transcript(op="update_title"|"update_privacy"|"update_channel", ...)`
- « partage cette réunion avec ces emails » → `fireflies_transcript(op="share", transcript_id="...", emails=[...])`
- « transcris ce fichier que j'ai en local » → `fireflies_transcript(op="create_upload_url", content_type="audio/mpeg", file_size=...)` puis, une fois les octets envoyés à l'URL rendue, `op="confirm_upload"` — flux non documenté par Fireflies, découvert en live via introspection (le PUT des octets se fait hors agent, comme pour Grain)
- « quelles réunions sont en cours d'enregistrement ? » → `fireflies_live_meeting(op="list_active")`
- « fais rejoindre le bot Fireflies à cette réunion Zoom » → `fireflies_live_meeting(op="add_bot", meeting_link="https://zoom.us/...")`
- « crée un action item / soundbite pendant la réunion en cours » → `fireflies_live_meeting(op="create_action_item"|"create_soundbite", meeting_id="...", prompt="...")`
- « qu'est-ce qui a été décidé sur le sujet X dans nos réunions récentes ? » → `fireflies_askfred(op="create_thread", query="...")` (cherche sur plusieurs réunions ; passe `transcript_id` pour cibler une seule réunion)
- « et qui s'en occupe ? » (suite de la question précédente) → `fireflies_askfred(op="continue_thread", thread_id="...", query="...")`
- « qui sont mes collègues / dans quels groupes ? » → `fireflies_user(op="list"|"groups")`
- « quels canaux existent ? » → `fireflies_channel(op="list")`
- « coupe ce passage en clip » → `fireflies_bite(op="create", transcript_id="...", start_time=120, end_time=145)`
- « les stats de participation de l'équipe ce mois-ci » → `fireflies_org(op="analytics", start_time="...", end_time="...")`
- « qui ai-je rencontré récemment ? » → `fireflies_org(op="contacts")`

## note — pas de gestion des webhooks ici

Fireflies configure ses webhooks (V1 comme V2) **exclusivement depuis son interface web** (Settings, puis Integrations → API → Webhook) — il n'existe aucune query ni mutation GraphQL pour créer/lister/supprimer un abonnement webhook. Ce connecteur n'a donc volontairement aucun tool `*_webhook` : la configuration se fait directement sur [app.fireflies.ai](https://app.fireflies.ai).

## note — testé en live, 3 bugs corrigés

Construit à partir de la documentation Fireflies (aucun spec OpenAPI accessible — `docs.fireflies.ai/api-reference/openapi.json` est listé dans le sitemap mais rend 404 au fetch). L'API live, elle, expose l'**introspection GraphQL** — testé le 2026-08-20 contre une vraie clé, 27/30 méthodes conformes du premier coup, 3 vrais bugs corrigés (type d'argument erroné sur `list_transcripts`, champ inexistant sur `get_askfred_thread`, faute de frappe côté API elle-même sur `createBite`).

⚠️ **Fireflies lui-même ne valide PAS l'existence d'un `channel_id`** sur `op="update_channel"` — assigner un id de canal inexistant réussit silencieusement côté Fireflies, et aucune mutation ne permet de « désassigner » un canal une fois posé. Ce connecteur s'en protège : `fireflies_transcript(op="update_channel", ...)` vérifie l'id contre `fireflies_channel(op="list")` et refuse un id inconnu AVANT d'appeler Fireflies — inutile de le faire toi-même.
