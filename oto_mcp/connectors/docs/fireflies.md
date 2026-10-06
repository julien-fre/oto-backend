## prerequisite — fireflies api key

create an API key in Fireflies (app.fireflies.ai → Integrations → API — see the [integration docs](https://docs.fireflies.ai)), then paste it into oto.
- byo-only: no shared oto key
- limits per tier: Free 50 requests/day, Pro 500/day, Business/Enterprise 60/min — some mutations have their own stricter limit (e.g. deletion 10/min, sharing 10/hour)

## usage — transcripts, live meeting, AskFred, org

fireflies transcribes your meetings and gives access to transcripts, control of a meeting IN PROGRESS, its Q&A assistant AskFred and organization data, in 7 tools:
- "find my meetings about the budget from last week" → `fireflies_transcript(op="list", keyword="budget", mine=True)`
- "the full summary and action items of this meeting" → `fireflies_transcript(op="get", transcript_id="...")` (summary, sentences, analytics, participants — all in one call)
- "transcribe this audio file" → `fireflies_transcript(op="upload", url="https://...", title="...")` — the URL must be publicly accessible
- "rename / make private / file this meeting in a channel" → `fireflies_transcript(op="update_title"|"update_privacy"|"update_channel", ...)`
- "share this meeting with these emails" → `fireflies_transcript(op="share", transcript_id="...", emails=[...])`
- "transcribe this file I have locally" → `fireflies_transcript(op="create_upload_url", content_type="audio/mpeg", file_size=...)` then, once the bytes have been sent to the returned URL, `op="confirm_upload"` — flow undocumented by Fireflies, discovered live via introspection (the PUT of the bytes is done outside the agent, as for Grain)
- "which meetings are being recorded right now?" → `fireflies_live_meeting(op="list_active")`
- "make the Fireflies bot join this Zoom meeting" → `fireflies_live_meeting(op="add_bot", meeting_link="https://zoom.us/...")`
- "create an action item / soundbite during the ongoing meeting" → `fireflies_live_meeting(op="create_action_item"|"create_soundbite", meeting_id="...", prompt="...")`
- "what was decided on topic X in our recent meetings?" → `fireflies_askfred(op="create_thread", query="...")` (searches across several meetings; pass `transcript_id` to target a single meeting)
- "and who is handling it?" (follow-up to the previous question) → `fireflies_askfred(op="continue_thread", thread_id="...", query="...")`
- "who are my colleagues / which groups?" → `fireflies_user(op="list"|"groups")`
- "which channels exist?" → `fireflies_channel(op="list")`
- "cut this passage into a clip" → `fireflies_bite(op="create", transcript_id="...", start_time=120, end_time=145)`
- "the team's participation stats this month" → `fireflies_org(op="analytics", start_time="...", end_time="...")`
- "who have I met recently?" → `fireflies_org(op="contacts")`

## note — no webhook management here

Fireflies configures its webhooks (V1 as well as V2) **exclusively from its web interface** (Settings, then Integrations → API → Webhook) — there is no GraphQL query or mutation to create/list/delete a webhook subscription. This connector therefore deliberately has no `*_webhook` tool: configuration is done directly on [app.fireflies.ai](https://app.fireflies.ai).

## note — tested live, 3 bugs fixed

Built from the Fireflies documentation (no accessible OpenAPI spec — `docs.fireflies.ai/api-reference/openapi.json` is listed in the sitemap but returns 404 on fetch). The live API, however, exposes **GraphQL introspection** — tested on 2026-08-20 against a real key, 27/30 methods conforming on the first try, 3 real bugs fixed (wrong argument type on `list_transcripts`, nonexistent field on `get_askfred_thread`, typo on the API's own side on `createBite`).

⚠️ **Fireflies itself does NOT validate that a `channel_id` exists** on `op="update_channel"` — assigning a nonexistent channel id silently succeeds on the Fireflies side, and no mutation allows "unassigning" a channel once set. This connector protects against it: `fireflies_transcript(op="update_channel", ...)` checks the id against `fireflies_channel(op="list")` and refuses an unknown id BEFORE calling Fireflies — no need to do it yourself.
