## prerequisite — aircall API ID + API Token

an aircall admin creates a key in the Aircall Dashboard (Company Settings → API Keys → *Add a new API key*), then pastes **both** values into oto: the `API ID` and the `API Token`. Reference: [Aircall API authentication](https://developer.aircall.io/api-references/#basic-auth-aircall-customers).
- ⚠️ **the token is shown only ONCE**, at creation: Aircall does not keep it in clear text. If it is lost, a new key must be created
- a key gives access to the **whole company**: Aircall offers no finer scope
- byo-only: no shared oto key — these are your company's calls, each organization sets its own
- transcription, summary, topics, sentiment and action items are only served to companies subscribed to **Aircall's AI offer** (AI Assist or AI Assist Pro)

## usage — what happened on the phone

- "yesterday's calls" → `aircall_calls(date_from="…", date_to="…", order="desc")`
- "the calls with this customer" → `aircall_calls(op="search", phone_number="+33…")`; "the calls of a given agent" → `op="search", user_id=…`, the id coming from `aircall_users`
- "the recording of this call" → `aircall_calls(op="get", call_id=…)`: `recording` (or `voicemail`) is an mp3 link
- "what did we talk about?" → `aircall_call_ai(call_id=…, op="summary")`, then `op="transcription"` for the word-for-word, `op="action_items"` for the follow-ups
- "who is this number?" → `aircall_contacts(op="search", phone_number="+33…")`
- the connector **never writes** to Aircall

## note — what can mislead

- ⚠️ **recording and voicemail links expire**: 1 hour for `recording`/`voicemail`, 3 hours for their short versions (`short_urls=true`). Request them again right before use, never store them
- ⚠️ **only six months of call history**, and at most 10,000 calls or contacts per pagination: narrow with `date_from` to go further
- `duration` includes ringing: the talk time is `ended_at - answered_at`. The API's dates are in UNIX seconds, UTC
- contacts synced from a third-party integration (CRM…) are **not** served by the API, even if they show up in Aircall
- a missing transcription or summary (404): the call was not analyzed, or the company does not have the AI offer

## note — usage limits

120 requests per minute for the whole company. The connector waits for the counter to reset when it is close, once only; otherwise it says so, and you just need to retry a minute later.
