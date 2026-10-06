## prerequisite — granola api key

create an API key in Granola (desktop app → Settings → Connectors → API keys → Create new key — see the [integration doc](https://docs.granola.ai/help-center/sharing/integrations/granola-api)), then paste it into oto.
- byo-only: no shared oto key
- personal key (any member of a Business plan) or workspace key (provisioned by an admin, Enterprise plans) — both work the same way here, Granola applies the scope on its side

## usage — meeting notes, transcripts, folders, webhooks

granola gives access to the meeting notes, transcripts and AI summaries of the connected workspace, through two tools:
- "what are my latest meeting notes?" → `granola_content(op="list_notes", created_after="2026-08-01")`
- "give me the summary and participants of this meeting" → `granola_content(op="get_note", note_id="not_...")`
- "the full transcript is too long" → `granola_content(op="get_transcript", note_id="not_...")` (dedicated pagination, handles `TRANSCRIPT_TOO_LARGE`)
- "which folders do I have?" → `granola_content(op="list_folders")`
- "notify my external system on every new note" → `granola_webhook_endpoint(op="create", url="https://...", scopes=["workspace"])` (`["personal"]`/`["public"]` with a personal key — a workspace key MUST pass exactly `["workspace"]`, confirmed live)
- "which webhooks have I already configured / disable this one" → `granola_webhook_endpoint(op="list"|"update"|"delete", ...)`

## note — pagination, `signing_secret`, and `scopes` reach

- all lists are paginated by `cursor` (returned in `hasMore`/`cursor` of each response, never a page number); `page_size` bounds: notes/folders 1-30 (default 10), transcript 1-100 (default 50)
- `granola_webhook_endpoint(op="create")` returns a `signing_secret` (HMAC-SHA256, Standard Webhooks format) **only once, in this response** — to be stored on the receiver side to verify deliveries; it is never reissued
- `scopes` determines WHICH notes trigger events for an endpoint: `personal` (notes owned or directly shared), `public` (notes visible to the whole workspace) — a workspace key must pass exactly `["workspace"]`
- Granola rate limits: 25 requests/5s burst, 5 req/s (300/min) sustained — beyond that, `429`
- verified word for word against Granola's OpenAPI 3.1.0 spec (`docs.granola.ai/api-reference/openapi.json`), not against a doc page summary — **and tested live on 2026-08-20** against a real workspace (workspace key): notes/transcript/folders + the full create→update→delete cycle of a webhook endpoint work as coded, 400 errors included
- the spec also documents `GET /v1/audit` (audit log); tested live, it returned `404 NOT_FOUND` on this key (probably a plan feature not enabled for this workspace) — removed from the connector rather than exposing a tool that nobody can currently use
