## prerequisite — grain api key

create an API key in Grain (app → Settings → Integrations → API — see the [integration doc](https://developers.grain.com/)), then paste it into oto.
- byo-only: no shared oto key
- Personal Access Token (per user) or Workspace Access Token (admin, access to all workspace data) — both work the same way here, Grain applies the scope on its side

## usage — meetings, transcripts, sharing, webhooks

grain gives access to meeting recordings, transcripts and organization data, through 5 tools:
- "what are MY latest meetings?" → `grain_recording(op="list", filter={"attendance": "hosted"})` — see the scope warning below, `attendance` is what restricts to "mine"
- "give me the summary and highlights of this meeting" → `grain_recording(op="get", recording_id="...", include={"ai_summary": true, "highlights": true})`
- "full transcript as plain text / subtitles" → `grain_transcript(recording_id="...", format="txt"|"vtt"|"srt")`
- "rename / tag / share this meeting" → `grain_recording(op="update"|"tag"|"share_user"|"share_team", recording_id="...", ...)`
- "download this meeting's file" → `grain_recording_file(op="download", recording_id="...")`
- "notify my external system on every new meeting" → `grain_hook(op="create", hook_url="https://...", hook_type="recording_added")`
- "also notify me of new highlights, with the transcript" → `grain_hook(op="create", hook_url="https://...", hook_type="highlight_added", include={"transcript": true})`
- "which teams / users / meeting types exist?" → `grain_org(op="users"|"teams"|"meeting_types")`
- "this team's Customer Support meetings that talk about onboarding" → `grain_recording(op="list", filter={"team": "...", "meeting_type": "...", "title_search": "onboarding"})` — the three filters combine, confirmed live (ids via `grain_org`)

## note — ⚠️ a Personal Access Token ALSO sees other people's meetings

**Verified live on 2026-08-20** on a real workspace: `grain_recording(op="list")` without an `attendance` filter returns the `share_state="public"` recordings of the **whole organization**, not only those of the token holder — observed concretely: meetings recorded by colleagues, where the PAT holder was not even a participant, came up first (chronological sort). This is the behavior documented by Grain ("Personal notes" + "Public notes: workspace-visible notes"), not a bug — but it is surprising if you expect a "my meetings only" scope.

**To scope to "my meetings"**: pass `filter={"attendance": "hosted"}` (meetings hosted by the token holder) or `"attended"` (meetings they took part in) — confirmed live, these two values filter correctly. Without this filter, an agent that lists carelessly can surface a colleague's customer call in an answer.

`list_users` also returns the full workspace directory (all members, not only the token holder) — normal, it is a directory, not meeting content.

## note — verification & fixed bug

- **tested live on 2026-08-20** with a real Personal Access Token (a customer workspace): 20 of the client's 21 methods worked first time — list/get recordings, the 4 transcript formats, tag/untag, share/unshare user, update (rename), download (real file, 21 MB), create_upload_url (real pre-signed S3 URL), and the full webhook cycle (create against a real reachable URL, list, delete)
- **one real bug was found and fixed**: `share_with_team` expected `team_id` in the JSON body (like `share_with_user`), not in the URL path as the doc initially suggested — the documented form really returns 404. Fixed and locked in again by a test
- no OpenAPI spec is accessible for this API (openapi.json/docs.json/mint.json/llms.txt all return 403 on developers.grain.com, a constant WAF block) — the initial build came from reading doc pages, now largely confirmed by the live test above
- `grain_recording(op="get")` uses a POST on Grain's side (not GET) — confirmed by both the doc and the live test
- mutations (`tag`/`untag`, `update`, `share_*`/`unshare_*`, webhook deletion) return `{"success": true}`, not the updated object — confirmed live
- highlights are NOT a separate tool — they appear in `grain_recording` via `include={"highlights": true}`
- `grain_hook` covers Grain's **whole** webhook surface — `list`/`create`/`delete` is all that exists on Grain's side (no PATCH/update, confirmed on the doc page, this is not partial coverage)
- `grain_hook(op="create")`: Grain tests the reachability of `hook_url` at creation — the URL must answer `2xx` immediately or the call fails (confirmed live with a real test URL); for `hook_type="highlight_added"|"highlight_updated"`, `include` accepts `{"transcript": bool, "speakers": bool}` (confirmed on the doc); `story_*` events are the ONLY way to observe Grain Stories — no REST endpoint lists/fetches them directly, the webhook payload IS the data
- `include={"hubspot": true}` on `grain_recording` returns `{"hubspot_company_ids": [...], "hubspot_deal_ids": [...]}`; `include={"participants": true}` also carries `hs_contact_id` per participant (null if not linked) — confirmed live
- file upload (raw bytes) is deliberately not exposed as an MCP tool — `grain_recording_file(op="create_upload_url")` gives the URL, sending the bytes happens outside the agent (the oto-core client carries `upload_recording_file` for scripted use; verified: it is a pre-signed S3 URL on a DIFFERENT host than api.grain.com, the Grain Bearer is never sent to it)
- upstream error format is not documented by Grain, and no upstream error was deliberately triggered during the live test — the error messages here stay generic (HTTP code) until a real error case has shown the actual shape
- Grain rate limit: 300 requests/minute, all routes combined
