## prerequisite — an Allo API key and its scopes

In the Allo web app, an Admin or a Manager opens **Settings → API** and creates a key, then pastes it into oto. The key's **scopes** bound what the connector reaches: conversations read for calls and texts, CRM read and write for the contacts, SMS send for texts. `allo(op="me")` lists the scopes a key has.
- byo-only: no shared oto account — these are your team's calls and contacts, each organization sets its own key
- one key covers the whole team: every line, member and contact the team holds
- the "test connection" button reads the key itself: no side effect

## usage — calls, contacts, the dialer

- "the calls this week that mention pricing" → `allo(op="search", item_type="CALL", search="pricing", date_from="2026-10-12", date_to="2026-10-18")`
- "the summary and transcript of this call" → `allo(op="item", item_id="cll-…", transcript=true)`
- "a link to the recording" → `allo(op="recording", item_id="cll-…")`
- "tag this call as meeting booked" → `allo_act(op="add_tags", item_id="cll-…", tags=["meeting_booked"])`
- "load these prospects in my Power Dialer" → `allo_act(op="add_queue_numbers", entries=[{"number": "+33612345678", "name": "Marie", "company": "Acme"}])`
- "how did the team do last week" → `allo(op="metrics", date_from="2026-10-12", date_to="2026-10-18")`
- "my outbound funnel, meetings as conversions" → `allo(op="outbound", date_from="2026-10-01", date_to="2026-10-20", tags=["meeting_booked"])`
- "add this contact to Allo" → `allo_act(op="create_person", fields={"numbers": ["+33612345678"], "name": "Marie", "last_name": "Durand"})`
- "text them a thank-you" → `allo_act(op="send_sms", from_number="+1415…", to="+1415…", message="…")`

## note — numbers, ids, what is not served

- phone numbers are E.164 (`+33612345678`); a conversation is a contact's number on one of the team's lines (`allo_number`, listed by `op="numbers"`)
- item ids: `cll-*` for a call, `msg-*` for a text; only a call has a summary, tags and a recording
- a recording link plays without an Allo login for 60 minutes: share it with nobody who may not hear the call
- a text is sent at once and billed per segment by Allo; in France it goes out from a Sender ID verified by Allo
- `create_queue` replaces the current Power Dialer queue with an empty one
- **not served**: webhooks, call flows, the AI receptionist, summary templates, threads, custom field definitions (read-only here), partner accounts, eSIMs
