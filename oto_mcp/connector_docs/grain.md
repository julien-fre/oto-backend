## prerequisite — clé api grain

crée une clé API dans Grain (app → Settings → Integrations → API — voir la [doc d'intégration](https://developers.grain.com/)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée
- Personal Access Token (par user) ou Workspace Access Token (admin, accès à toutes les données du workspace) — les deux fonctionnent ici de la même façon, Grain applique le scope de son côté

## usage — réunions, transcripts, partage, webhooks

grain donne accès aux enregistrements de réunion, transcripts et données d'organisation, en 5 tools :
- « quelles sont MES dernières réunions ? » → `grain_recording(op="list", filter={"attendance": "hosted"})` — voir l'avertissement de portée ci-dessous, `attendance` est ce qui restreint à « les miennes »
- « donne-moi le résumé et les highlights de cette réunion » → `grain_recording(op="get", recording_id="...", include={"ai_summary": true, "highlights": true})`
- « transcript complet en texte brut / sous-titres » → `grain_transcript(recording_id="...", format="txt"|"vtt"|"srt")`
- « renomme / tague / partage cette réunion » → `grain_recording(op="update"|"tag"|"share_user"|"share_team", recording_id="...", ...)`
- « télécharge le fichier de cette réunion » → `grain_recording_file(op="download", recording_id="...")`
- « préviens mon système externe à chaque nouvelle réunion » → `grain_hook(op="create", hook_url="https://...", hook_type="recording_added")`
- « préviens-moi aussi des nouveaux highlights, avec le transcript » → `grain_hook(op="create", hook_url="https://...", hook_type="highlight_added", include={"transcript": true})`
- « quelles équipes / quels users / quels types de réunion existent ? » → `grain_org(op="users"|"teams"|"meeting_types")`
- « les réunions Customer Support de cette équipe qui parlent d'onboarding » → `grain_recording(op="list", filter={"team": "...", "meeting_type": "...", "title_search": "onboarding"})` — les trois filtres se combinent, confirmé en live (ids via `grain_org`)

## ⚠️ note — un Personal Access Token voit AUSSI les réunions des autres

**Vérifié en live le 2026-08-20** sur un vrai workspace : `grain_recording(op="list")` sans filtre `attendance` rend les enregistrements `share_state="public"` de **toute l'organisation**, pas seulement ceux du porteur du token — constaté concrètement : des réunions enregistrées par des collègues, où le porteur du PAT n'était même pas participant, sont remontées en premier (tri chronologique). C'est le comportement documenté par Grain (« Personal notes » + « Public notes : workspace-visible notes »), pas un bug — mais ça surprend si on s'attend à un scope « mes réunions seulement ».

**Pour scoper à « mes réunions »** : passer `filter={"attendance": "hosted"}` (réunions animées par le porteur du token) ou `"attended"` (réunions auxquelles il a participé) — confirmé en live, ces deux valeurs filtrent correctement. Sans ce filtre, un agent qui liste sans précaution peut faire remonter l'appel client d'un collègue dans une réponse.

`list_users` rend aussi l'annuaire complet du workspace (tous les membres, pas seulement le porteur) — normal, c'est un annuaire, pas du contenu de réunion.

## note — vérification & bug corrigé

- **testé en live le 2026-08-20** avec un Personal Access Token réel (workspace folk.app) : 20 des 21 méthodes du client ont marché du premier coup — list/get recordings, les 4 formats de transcript, tag/untag, share/unshare user, update (renommage), download (fichier réel, 21 Mo), create_upload_url (vraie URL S3 pré-signée), et le cycle complet des webhooks (create contre une URL joignable réelle, list, delete)
- **un vrai bug a été trouvé et corrigé** : `share_with_team` attendait `team_id` dans le corps JSON (comme `share_with_user`), pas dans le chemin de l'URL comme la doc le suggérait au départ — la forme documentée 404 réellement. Corrigé et reverrouillé par un test
- aucun spec OpenAPI n'est accessible pour cette API (openapi.json/docs.json/mint.json/llms.txt rendent tous 403 sur developers.grain.com, un blocage WAF constant) — la construction initiale venait d'une lecture de pages de doc, désormais confirmée en grande partie par le test live ci-dessus
- `grain_recording(op="get")` utilise un POST côté Grain (pas GET) — confirmé à la fois par la doc et par le test live
- les mutations (`tag`/`untag`, `update`, `share_*`/`unshare_*`, suppression de webhook) rendent `{"success": true}`, pas l'objet mis à jour — confirmé en live
- les highlights ne sont PAS un tool séparé — ils apparaissent dans `grain_recording` via `include={"highlights": true}`
- `grain_hook` couvre **toute** la surface webhooks de Grain — `list`/`create`/`delete` est tout ce qui existe côté Grain (pas de PATCH/update, confirmé sur la page de doc, ce n'est pas une couverture partielle)
- `grain_hook(op="create")` : Grain teste la joignabilité de `hook_url` à la création — l'URL doit répondre `2xx` immédiatement ou l'appel échoue (confirmé en live avec une URL de test réelle) ; pour `hook_type="highlight_added"|"highlight_updated"`, `include` accepte `{"transcript": bool, "speakers": bool}` (confirmé sur la doc) ; les événements `story_*` sont le SEUL moyen d'observer les Stories Grain — aucun endpoint REST ne les liste/récupère directement, la charge utile du webhook EST la donnée
- `include={"hubspot": true}` sur `grain_recording` rend `{"hubspot_company_ids": [...], "hubspot_deal_ids": [...]}` ; `include={"participants": true}` porte aussi `hs_contact_id` par participant (null si non lié) — confirmé en live
- l'upload de fichier (octets bruts) n'est volontairement pas exposé comme un tool MCP — `grain_recording_file(op="create_upload_url")` donne l'URL, l'envoi des octets se fait hors agent (le client oto-core porte `upload_recording_file` pour un usage scripté ; vérifié : c'est une URL S3 pré-signée sur un AUTRE host qu'api.grain.com, jamais le Bearer Grain envoyé dessus)
- format d'erreur amont non documenté par Grain, et aucune erreur amont n'a été déclenchée volontairement pendant le test live — les messages d'erreur ici restent génériques (code HTTP) tant qu'un vrai cas d'erreur n'a pas montré la forme réelle
- limite de débit Grain : 300 requêtes/minute, toutes routes confondues
