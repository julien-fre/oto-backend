## prerequisite — a Mux access token

in the Mux dashboard, open the **environment** that holds (or will hold) the videos, then Settings → Access Tokens → *Generate new token*, with **Mux Video** read and write. Paste both values into oto: the `Access Token ID` and the `Secret Key`. Reference: [Mux API authentication](https://docs.mux.com/core/make-api-requests).
- ⚠️ **the secret key is shown only ONCE**, at creation. If it is lost, generate a new token
- a token sees **one environment** only: a video from another environment answers "not found"
- byo-only: these are your videos, billed to your Mux account

## usage — get a video in, get its playback link

- "upload this film" → `mux_act(op="create_upload", title="…", max_resolution_tier="1440p")`, then send the file to the answer's `data.url` in ONE PUT, from wherever it is (e.g. `curl -X PUT --upload-file film.mp4 "<url>"`): the file never passes through oto
- "has it arrived?" → `mux(op="get_ingest", upload_id=…)`: `asset_created` with its `asset_id`
- "is it ready, and what is its link?" → `mux(op="get_asset", asset_id=…)`: `status` `ready`, `duration`, and `stream_url` on each public playback id
- "put this file online" when it already has an https address → `mux_act(op="create_asset", input_url="https://…")`
- "our latest videos" → `mux(op="list_assets", limit=10)`

## note — what can mislead

- an upload address **expires** after `timeout` seconds (one hour by default) if no file is sent
- a video is `preparing` for about its own length before it is `ready`; it plays only then
- each `mux_act` call makes a **new** video: nothing is deduplicated, and Mux bills storage and delivery per video
- `test=true` makes a free 10-second watermarked video deleted after a day: for trying the flow, not for a real video
- a `signed` playback needs tokens this connector does not make: keep `public` unless the videos are private
