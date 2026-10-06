## prerequisite — API Key ID + Key Secret leexi

generate a key pair in Leexi (Settings → Company Settings → API Keys → *add*), then paste **both** values into oto: the `API Key ID` and the `Key Secret`.
- ⚠️ **the secret is shown only ONCE**, at creation — if it is lost, a new pair must be created
- **Leexi admin account required** to create a key: it is not a setting an ordinary user can change
- byo-only: no shared oto key — these are your company's recorded calls, each organization sets its own
- ⚠️ **a new key only carries `read_calls`.** Everything else — `read_users`, `read_teams`, `read_meeting_events`, `write_calls`, `write_meeting_events`, and above all `write_users` / `write_teams` **which commit your billed licenses** — must be explicitly ticked by an admin. The "test the connection" button therefore settles for `read_calls`: it is the only scope a default key has, and probing elsewhere would make a healthy key pass for a dead one
- a **call access scope** is also set on the Leexi side, next to the scopes: the whole company (default), a given user's access, or access rules. It decides which calls the key sees

## usage — what was said on the phone, and what was retained from it

- "what did we talk about with this customer?" → `leexi_calls(op="search", customer_email_address=["…"])` then `op="get"` on the returned uuid: `get` is what returns the **transcript** and the topics, `search` only has the metadata
- "the minutes of this meeting" → `leexi_notes(op="list", call_uuid="…")` — notes are the outputs of Leexi prompts, and that is where the summary lives, rather than in the raw transcript
- "this week's calls" → `leexi_calls(op="search", date_filter="performed_at", date_from="…", date_to="…")`
- "this salesperson's calls" → `leexi_calls(op="search", owner_uuid=["…"])`, the uuid coming from `leexi_users(op="list")`
- "record this upcoming meeting" → `leexi_meetings(op="create", fields={…})`, then `op="launch_bot"` to send the assistant to it
- import an existing recording → `leexi_calls(op="presign", extension="mp3")`, upload the file to the returned URL, then `leexi_calls(op="create", fields={… "recording_s3_key": "…"})`

## note — four things that mislead

- ⚠️ **an empty list is not necessarily an error**: if the key's access scope covers no call, `leexi_calls(op="search")` returns an empty list, and that is a valid setting. Likewise, a **404** on `op="get"` can mean "outside this key's scope", not "does not exist" — Leexi answers 404 on purpose for what a key is not allowed to see
- ⚠️ **a call that was just created does not have its notes yet**: creation is asynchronous (a few minutes), and the prompt completions — summary, chaptering — arrive AFTER. Re-read later rather than conclude they are missing
- ⚠️ **`leexi_users(op="deactivate")` deletes nothing**: calls and history stay, sessions drop, and the license is freed. To reactivate, `op="update"` with `{"active": true}` — which takes back a billed license
- ⚠️ **a team that still carries users or calls cannot be deleted** (422): deactivate it with `leexi_teams(op="update", fields={"active": false})`, which the vendor recommends

## note — usage limits

50 requests/minute, and only **10/minute for call creation**. A bulk import must therefore be spread out; the connector honors Leexi's `Retry-After` on reads, but never replays a write (the API has no idempotency key, and a replay would create a duplicate).
