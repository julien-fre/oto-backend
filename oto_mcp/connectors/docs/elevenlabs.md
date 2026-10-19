## prerequisite — an ElevenLabs API key

create a key in ElevenLabs (Developers → API Keys → *Create key*) and paste it into oto. Reference: [ElevenLabs authentication](https://elevenlabs.io/docs/api-reference/authentication).
- a key can be **restricted** when it is created: the generations need *Text to Speech*, *Music* and *Sound Effects*; the reads need *User* read (the credits), *Voices* read and *Models* read. A missing permission is named in the refusal
- a key can carry its own **credit limit** in ElevenLabs: a good way to give a teammate or an agent a budget
- byo-only: every generation spends **your** ElevenLabs credits; each organization or person sets its own key

## usage — a voice, a track, a sound

- "how many credits are left?" → `elevenlabs(op="get_subscription")`: `character_count` used against `character_limit`, and when it resets
- "which voices can I use?" → `elevenlabs(op="list_voices", search="warm")`; one voice's details → `op="get_voice", voice_id=…`
- "read this line" → `elevenlabs_act(op="create_speech", voice_id=…, text="…")`; add `timestamps=true` for each word's start and end (captions, cutting a video on a word)
- a script read line by line sounds continuous when each call passes the neighbouring lines as `previous_text` and `next_text`
- "a 45-second track" → `elevenlabs_act(op="create_music", prompt="…", music_length_ms=45000)`, or a `composition_plan` that sets each section's length and styles, including what must NOT happen (`negative_global_styles`)
- "a soft click" → `elevenlabs_act(op="create_sound", text="…", duration_seconds=1)`
- "find a narrator's voice" → `elevenlabs_act(op="create_voice_design", voice_description="…", text="…")`: three previews to listen to

## note — what can mislead

- ⚠️ the audio is a **download link that expires** (`expires_in`, one hour by default; the file is deleted after a day): download it right away, never store the link
- every `elevenlabs_act` call spends credits and is **never retried** automatically: a replay bills twice
- an empty account answers like a refused key on ElevenLabs' side (401); the connector tells the two apart and says which
- `seed` makes a take reproducible as far as the model allows, not exactly
- a voice design preview is **not** a voice of the account: saving it takes a voice slot, in ElevenLabs
- some output formats (`mp3_44100_192`, `pcm_44100`) and some voices are paid plans only (403)
