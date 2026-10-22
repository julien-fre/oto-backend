## prerequisite — an Allo API key and its scopes

In the Allo web app, an Admin or a Manager opens **Settings → API** and creates a key, then pastes it into oto. The key's **scopes** bound what the connector reaches: conversations read for calls and texts, CRM read and write for the contacts, SMS send for texts. `allo(op="me")` lists the scopes a key has.
- byo-only: no shared oto account — these are your team's calls and contacts, each organization sets its own key
- one key covers the whole team: every line, member and contact the team holds
- the "test connection" button reads the key itself: no side effect

## usage — calls, contacts, the dialer

- "the calls this week that mention pricing" → `allo(op="search", item_type="CALL", search="pricing", date_from="2026-10-12", date_to="2026-10-18")`
- "the summary and transcript of this call" → `allo(op="item", item_id="cll-…", transcript=true)`
- "the recording of this call" → `allo(op="recording", item_id="cll-…")`: the audio, kept one day, as a short `source` (`{"kind":"transit","ref":…}`)
- "transcribe this call" → `transcription_create(source=<that source, as is>, _project=…)`
- "tag this call as meeting booked" → `allo_act(op="add_tags", item_id="cll-…", tags=["meeting_booked"])`
- "load these prospects in my Power Dialer" → `allo_act(op="add_queue_numbers", entries=[{"number": "+33199000001", "name": "Marie", "company": "Acme"}])`
- "how did the team do last week" → `allo(op="metrics", date_from="2026-10-12", date_to="2026-10-18")`
- "my outbound funnel, meetings as conversions" → `allo(op="outbound", date_from="2026-10-01", date_to="2026-10-20", tags=["meeting_booked"])`
- "add this contact to Allo" → `allo_act(op="create_person", fields={"numbers": ["+33199000001"], "name": "Marie", "last_name": "Durand"})`
- "text them a thank-you" → `allo_act(op="send_sms", from_number="+14155550100", to="+14155550123", message="…")` — one sender: `from_number` OR `sender_id`, never both
- "create a « Note d'appel » field on contacts" → `allo_act(op="create_attribute", entity="person", label="Note d'appel", attribute_type="TEXT")`, then write it with `allo_act(op="update_person", person_id="per-…", fields={"custom_attributes": {"note_d_appel": "…"}})` (the key is in `allo(op="attributes", entity="person")`)
- "load these prospects in Gabriel's Power Dialer" → read `allo(op="queue", email="gabriel@…")` first (Allo does not deduplicate a queue), then `allo_act(op="add_queue_numbers", email="gabriel@…", entries=[…])`
- "ping me on calls tagged demo" → `allo(op="webhook_events")`, then `allo_act(op="create_webhook", url="https://…", topics=["tag.added"])`
- "change the receptionist's greeting" → `allo(op="receptionist", allo_number="+33…")`, then `allo_act(op="update_receptionist", allo_number="+33…", fields={"greeting_message": "…"})`

## note — numbers, ids, what is not served

- phone numbers are E.164 (`+33199000001`); a conversation is a contact's number on one of the team's lines (`allo_number`, listed by `op="numbers"`)
- item ids: `cll-*` for a call, `msg-*` for a text; only a call has a summary, tags and a recording
- ⚠️ **a call carries no recording link** (`search`, `item`, `items`, `conversations`, even with `full=true`): Allo's signed links play without a login, so none is served; the audio comes only through `op="recording"`, 50 MB at most
- lists drop by default a call's AI blobs (`extracted_data`, `data_collected`), a record's `interactions`, a note's `mentions` (named under `projection`): `full=true` returns them
- a text (1000 characters at most) is sent at once and billed per segment by Allo: 160 characters, or 70 as soon as one is outside the standard SMS alphabet (ç, ê, an emoji); in France it goes out from a Sender ID verified by Allo
- `create_queue` replaces the current Power Dialer queue with an empty one; `add_queue` adds a named queue and keeps the current one; any queue is reached by `queue_id` (`allo(op="queues")`)
- ⚠️ `delete_attribute` on a **company** attribute erases its values on every company (a person field keeps them, and re-creating it restores them); `publish_call_flow` makes the draft the line's live routing at once; `set_receptionist_status` puts the AI receptionist on the line or takes it off
- a webhook's `signing_secret` comes back on `create_webhook` and `allo(op="webhook")`: it is what the receiver checks deliveries with
- **not served**: partner accounts and eSIMs (a reseller key, not a team key), the v1 endpoints that v2 replaces
