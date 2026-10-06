## prerequisite — a Mistral key, training disabled

create an API key on the [Mistral console](https://console.mistral.ai/api-keys), then paste it into oto at the organization level.
- **before a customer's first recording**, disable the use of your data for training in the Mistral account settings: these are the voices of private individuals, often at home
- by default each organization pays for its own audio minute at Mistral (EU hosting); an org without its own key may be granted a platform instance (that instance's key, language and vocabulary), and its own instance always takes precedence

## setup — language and vocabulary

two optional settings on the instance, non-secret:
- **language**: the code of the spoken language (`fr` if empty). `auto` lets Mistral detect it
- **vocabulary**: the business words to spell correctly (structures, materials, proper names), separated by commas or line breaks. Mistral only takes isolated words: an expression is split into its words, and words under 3 letters are discarded ("pompe à chaleur" → `pompe`, `chaleur`). 100 words at most; what is discarded is reported in the response

one instance = one key × one language × one vocabulary; a project attaches to the desired instance through a slot.

## usage — a recording becomes a project page (asynchronous)

- "transcribe the visit dropped on the project" → `transcription_create(source={"kind":"project_file","project_id":…,"file_id":…}, _project=…)` — the ids come from `oto_project_files op=list`
- `vocabulary` (optional) supplements, for this recording, the instance's vocabulary (proper names, site materials); `vocabulary_replace=true` uses it alone. 100 words at most in total, the instance takes precedence
- the file is read server-side, never carried through the conversation (100 MB at most, up to 3 h of audio; a project file remains limited to 25 MB at upload)
- **the call does not block**: it immediately returns `{job_id, status:"pending"}` — a 30 min recording takes 20 s to 5 min to transcribe, as a background task
- re-read `transcription_status(job_id)` until `status:"done"` (or `"failed"` with `error`): that is where the page "Transcription — <file> — <date>" comes back (id, title, link), one paragraph per speaker turn with the speaker and the moment at the head ("Locuteur 1 [03:12]"…), the word count, the duration and the speakers — never the text, which is then read like any project page

## usage — from a program (REST API, `oto_…` token)

- the audio itself: `curl -H "Authorization: Bearer $OTO_TOKEN" -F file=@reunion.ogg -F vocabulary="Voxtral, Otomata" https://mcp.oto.cx/api/me/projects/<id>/transcriptions/upload` → `202 {job_id, status:"pending"}`
- a file oto already knows how to reach: `POST /api/me/projects/<id>/transcriptions` with `{"source":{"kind":"project_file","file_id":…}}` (or `url`, `drive`, `gmail`)
- re-read `GET /api/me/transcriptions/<job_id>`: on `done`, in addition to the page, `transcript` returns the speaker turns as data — `[{speaker, start, end, text}]`, in seconds

## note — what the connector corrects, and what it does not do

- identically repeated segments are merged; the "parasite" speakers that diarization invents for a single sentence are attached to the neighboring speaker (at most 3 speakers are kept, each weighing at least 10 % of the recording)
- nothing is transcribed without a call: you only pay for what is requested
- the transcription draws no conclusion from the text: it is the project's procedure that says what to do with it (visit sheet, report…)
