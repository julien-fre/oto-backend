## prerequisite — clé api granola

crée une clé API dans Granola (app desktop → Settings → Connectors → API keys → Create new key — voir la [doc d'intégration](https://docs.granola.ai/help-center/sharing/integrations/granola-api)), puis colle-la dans oto.
- byo-only : pas de clé oto partagée
- clé personnelle (tout membre d'un plan Business) ou clé workspace (provisionnée par un admin, plans Enterprise) — les deux fonctionnent ici de la même façon, Granola applique le scope de son côté

## usage — notes de réunion, transcripts, dossiers, webhooks

granola donne accès aux notes de réunion, transcripts et résumés IA de l'espace connecté, en deux tools :
- « quelles sont mes dernières notes de réunion ? » → `granola_content(op="list_notes", created_after="2026-08-01")`
- « donne-moi le résumé et les participants de cette réunion » → `granola_content(op="get_note", note_id="not_...")`
- « le transcript complet est trop long » → `granola_content(op="get_transcript", note_id="not_...")` (pagination dédiée, gère `TRANSCRIPT_TOO_LARGE`)
- « quels dossiers ai-je ? » → `granola_content(op="list_folders")`
- « préviens mon système externe à chaque nouvelle note » → `granola_webhook_endpoint(op="create", url="https://...", scopes=["workspace"])` (`["personal"]`/`["public"]` avec une clé personnelle — une clé workspace DOIT passer exactement `["workspace"]`, confirmé en live)
- « quels webhooks ai-je déjà configurés / désactive celui-ci » → `granola_webhook_endpoint(op="list"|"update"|"delete", ...)`

## note — pagination, `signing_secret`, et portée `scopes`

- toutes les listes sont paginées par `cursor` (rendu dans `hasMore`/`cursor` de chaque réponse, jamais de numéro de page) ; bornes `page_size` : notes/dossiers 1-30 (défaut 10), transcript 1-100 (défaut 50)
- `granola_webhook_endpoint(op="create")` rend un `signing_secret` (HMAC-SHA256, format Standard Webhooks) **une seule fois, dans cette réponse** — à stocker côté récepteur pour vérifier les livraisons ; il n'est jamais réémis
- `scopes` détermine QUELLES notes déclenchent des événements pour un endpoint : `personal` (notes possédées ou partagées directement), `public` (notes visibles par tout l'espace) — une clé workspace doit passer exactement `["workspace"]`
- limites de débit Granola : 25 requêtes/5s en rafale, 5 req/s (300/min) soutenu — au-delà, `429`
- vérifié mot pour mot contre le spec OpenAPI 3.1.0 de Granola (`docs.granola.ai/api-reference/openapi.json`), pas contre un résumé de page doc — **et testé en live le 2026-08-20** contre un vrai workspace (clé workspace) : notes/transcript/dossiers + le cycle complet création→modification→suppression d'un webhook endpoint fonctionnent tel que codé, erreurs 400 comprises
- le spec documente aussi `GET /v1/audit` (journal d'audit) ; testé en live, il a rendu `404 NOT_FOUND` sur cette clé (probablement une fonctionnalité de plan non activée pour cet espace) — retiré du connecteur plutôt que d'exposer un tool que personne ne peut actuellement utiliser
