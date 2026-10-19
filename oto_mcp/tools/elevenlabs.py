"""ElevenLabs — voices and models, the credit balance, and audio generation.

Wraps `oto.tools.elevenlabs.ElevenLabsClient` (`xi-api-key` header), key resolved
per call via `access.resolve_api_key("elevenlabs")` (BYOK, user or org).

**Surface** (the `<ns>` / `<ns>_act` pair):
- `elevenlabs` (read-only) — the account's credits, its voices, one voice, the
  models;
- `elevenlabs_act` — the four generations, each of which spends credits:
  speech (optionally with each word's start and end), music, a sound effect,
  and previews of a voice described in words.

Audio never travels inline: it is deposited in private temporary storage and
served as a signed URL (`media_store.upload_private`), valid
`media_store.presign_expiry()` seconds and deleted after a day.

Calls to the client are written out in full (`_client().create_speech(…)`): this is
what makes them verifiable by the version-skew probe
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import ACTE, LECTURE

_NAME = "elevenlabs"
_PREFIX = "elevenlabs-audio"

_READ_OPS = ("get_subscription", "list_voices", "get_voice", "list_models")
_ACT_OPS = ("create_speech", "create_music", "create_sound", "create_voice_design")

# Keys removed from the LISTS by default, at any depth: a voice's training data
# (samples, fine-tuning state), its library sharing record, verification and safety
# details, the per-tier and per-language matrices. `get_voice` keeps everything.
_DROP_VOICES = frozenset({
    "samples", "fine_tuning", "sharing", "verified_languages", "voice_verification",
    "high_quality_base_model_ids", "safety_control", "permission_on_resource",
    "available_for_tiers", "favorited_at_unix", "is_bookmarked",
})
_DROP_MODELS = frozenset({"languages", "concurrency_group", "model_rates",
                          "requires_alpha_access", "serves_pro_voices"})
_HINT = "full=true returns the raw records."


class ElevenLabsASec(RuntimeError):
    """The ElevenLabs account is out of credits. ElevenLabs says so with a 401
    (`detail.status = quota_exceeded`), which alone would read as a refused key: this
    carries `status_code = 402` ON PURPOSE, so the taxonomy marks the account empty,
    not the key dead (same device as `serper.SerperASec`)."""
    status_code = 402


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _slim(value: Any, drop: frozenset, dropped: set) -> Any:
    """Copy of `value` without the keys in `drop`, at any depth."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in drop:
                dropped.add(k)
                continue
            out[k] = _slim(v, drop, dropped)
        return out
    if isinstance(value, list):
        return [_slim(v, drop, dropped) for v in value]
    return value


def _view(payload: Any, full: bool, drop: frozenset) -> Any:
    """Tightened view of a list (unless `full`); the answer NAMES what it removed."""
    if full:
        return payload
    dropped: set = set()
    out = _slim(payload, drop, dropped)
    if dropped and isinstance(out, dict):
        out["projection"] = {"omitted": sorted(dropped), "hint": _HINT}
    elif dropped and isinstance(out, list):
        out = {"items": out, "projection": {"omitted": sorted(dropped), "hint": _HINT}}
    return out


def _detail_status(e) -> str:
    """`detail.status` of an ElevenLabs error body (`quota_exceeded`,
    `invalid_api_key`, `missing_permissions`…), or ''."""
    body = getattr(e, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail.get("status") or "") if isinstance(detail, dict) else ""


def _detail_message(e) -> str:
    body = getattr(e, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)[:400]
    return str(detail if detail is not None else body)[:400]


def _upstream_message(e) -> str:
    status, kind = e.status_code, _detail_status(e)
    if status == 401 and kind == "missing_permissions":
        return ("ElevenLabs: this key is restricted and lacks the permission this call "
                f"needs ({_detail_message(e)}). Widen the key's permissions in ElevenLabs, "
                "or use another key.")
    if status == 401:
        return ("ElevenLabs refused the API key (HTTP 401): missing, revoked or mistyped. "
                "Retrying unchanged fails the same way.")
    if status == 403:
        return ("ElevenLabs refused the operation (HTTP 403): the plan does not include "
                f"it ({_detail_message(e)}). Some voices, models and output formats are "
                "paid only.")
    if status == 404:
        return "ElevenLabs: not found (404). Check the voice id (`op='list_voices'`)."
    if status in (400, 422):
        return f"ElevenLabs rejected the request (HTTP {status}): {_detail_message(e)}"
    if status == 429:
        return ("ElevenLabs: limit reached (429), too many requests at once for the plan. "
                "Retry in a moment.")
    if status >= 500:
        return f"ElevenLabs is temporarily unavailable (HTTP {status}); retry later."
    return f"ElevenLabs refused the request (HTTP {status}): {_detail_message(e)}"


def _refuse_ignored(op: str, **provided) -> None:
    """An argument supplied that THIS op does not use is an intent error."""
    for name, value in provided.items():
        if value is not None and value is not False:
            raise _bad(f"op='{op}' does not use `{name}`. Nothing was done.")


def _need(value, name: str, op: str):
    if value is None or value == "":
        raise _bad(f"op='{op}' requires `{name}`.")
    return value


def words_of(alignment: Optional[dict]) -> list[dict]:
    """Characters with their times → words with theirs: `[{"word", "start", "end"}]`,
    seconds. A word is a run of non-space characters, punctuation kept on it;
    a performance tag in square brackets (`[warmly]`) is not a word."""
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict] = []
    cur: Optional[dict] = None
    in_tag = False
    for i, ch in enumerate(chars):
        if ch == "[":
            in_tag = True
        if in_tag:
            if ch == "]":
                in_tag = False
            continue
        if not ch.strip():
            if cur:
                words.append(cur)
            cur = None
            continue
        if i >= len(starts) or i >= len(ends):
            break
        if cur is None:
            cur = {"word": "", "start": starts[i], "end": ends[i]}
        cur["word"] += ch
        cur["end"] = ends[i]
    if cur:
        words.append(cur)
    return [{"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in words]


def _deposit(audio: dict, stem: str, sub: str) -> dict:
    """Audio bytes → private temporary storage, served as a signed URL."""
    from .. import media_store
    filename = f"{stem}.{audio['extension']}"
    try:
        url = media_store.upload_private(_PREFIX, sub, audio["content"],
                                         audio["content_type"], filename)
    except media_store.MediaError as e:
        raise _bad(f"ElevenLabs: the audio was generated but temporary storage is "
                   f"unavailable ({e}); nothing can be served.") from None
    out = {"filename": filename, "mimeType": audio["content_type"],
           "size": len(audio["content"]), "output_format": audio.get("output_format"),
           "encoding": "url", "url": url, "expires_in": media_store.presign_expiry()}
    for key in ("request_id", "character_count"):
        if audio.get(key) is not None:
            out[key] = audio[key]
    return out


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Probe for "test the connection": the subscription, the cheapest authenticated
    call, which also says whether credits are left this period."""
    from oto.tools.elevenlabs import ElevenLabsClient
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        sub = ElevenLabsClient(api_key=fields.get("key")).probe()
    except UpstreamHTTPError as e:
        if e.status_code == 401 and _detail_status(e) == "quota_exceeded":
            raise connector_verify.QuotaEpuise(f"ElevenLabs: {_detail_message(e)}")
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"ElevenLabs HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"ElevenLabs HTTP {e.status_code}: {e.body}")
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))
    used, limit = sub.get("character_count"), sub.get("character_limit")
    if isinstance(used, int) and isinstance(limit, int) and limit > 0 and used >= limit:
        raise connector_verify.QuotaEpuise(
            f"ElevenLabs: {used} of {limit} credits used this period.")


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.elevenlabs import ElevenLabsClient

    connector_verify.register(_NAME, _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client() -> ElevenLabsClient:
        key, _is_platform = access.resolve_api_key(_NAME)
        return ElevenLabsClient(api_key=key)

    def _run(fn):
        restricted = None
        try:
            return fn()
        except UpstreamHTTPError as e:
            if e.status_code == 401 and _detail_status(e) == "quota_exceeded":
                raise _bad("ElevenLabs: the account has no credits left for this period "
                           "(quota_exceeded). The call was correct: top up or wait for the "
                           "reset (`op='get_subscription'` of `elevenlabs` says when); do not "
                           "retry it.") from ElevenLabsASec(str(e))
            if e.status_code == 401 and _detail_status(e) == "missing_permissions":
                restricted = _upstream_message(e)
            else:
                raise _bad(_upstream_message(e))
        except ValueError as e:
            raise _bad(str(e))
        # A key restricted on purpose is not a dead key. Raised OUTSIDE the `except`:
        # `from None` would still leave the 401 in `__context__`, which the taxonomy
        # walks (`error_taxonomy`), and the key would be painted red.
        raise _bad(restricted)

    @mcp.tool(name="elevenlabs", annotations=LECTURE)
    def elevenlabs(
        op: Literal["get_subscription", "list_voices", "get_voice", "list_models"],
        voice_id: Optional[str] = None,
        search: Optional[str] = None,
        voice_type: Optional[Literal["personal", "community", "default", "workspace",
                                     "non-default"]] = None,
        category: Optional[Literal["premade", "cloned", "generated", "professional"]] = None,
        page_size: Optional[int] = None,
        next_page_token: Optional[str] = None,
        full: bool = False,
    ) -> Any:
        """ElevenLabs account, read-only: credits, voices and models. Nothing here
        spends credits; the generations are `elevenlabs_act`.

        `op`:
        - `get_subscription` — plan, credits used this period (`character_count`)
          against the allowance (`character_limit`), reset time
          (`next_character_count_reset_unix`), voice slots. Read it before a batch.
        - `list_voices` — the voices the account can use (its own and the default
          ones): id, name, category, labels (accent, age, gender, use case), preview
          URL. Narrow with `search`, `voice_type`, `category`; page with
          `next_page_token` while `has_more` is true.
        - `get_voice` — one voice (`voice_id`): labels, description, default settings.
        - `list_models` — the models and what each can do (languages, maximum
          characters per request). A model id is what `create_speech` takes.

        The two lists are tightened by default (a voice's samples, fine-tuning,
        sharing and verification records; a model's language and rate matrices),
        and the answer's `projection` names what was removed: `full=true` returns
        the raw records.

        A restricted key may lack the permission a read needs (`user_read`,
        `voices_read`, `models_read`) even when it can generate.

        Args:
            op: What to read (see above).
            voice_id: The voice to read, for `get_voice`.
            search: Words matched against a voice's name, description, labels and category (`list_voices`).
            voice_type: `personal` (the account's own), `community` (saved from the library), `default`, `workspace`, or `non-default` (`list_voices`).
            category: `premade`, `cloned`, `generated` or `professional` (`list_voices`).
            page_size: Voices per page, 1 to 100, default 10 (`list_voices`).
            next_page_token: From the previous page of `list_voices`.
            full: `list_voices` / `list_models`: the raw records instead of the tightened view.
        """
        if op not in _READ_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_READ_OPS)}.")
        if op != "list_voices":
            _refuse_ignored(op, search=search, voice_type=voice_type, category=category,
                            page_size=page_size, next_page_token=next_page_token)
        if op != "get_voice":
            _refuse_ignored(op, voice_id=voice_id)
        if op not in ("list_voices", "list_models"):
            _refuse_ignored(op, full=full)
        if op == "get_subscription":
            return _run(lambda: _client().get_subscription())
        if op == "list_models":
            return _view(_run(lambda: _client().list_models()), full, _DROP_MODELS)
        if op == "get_voice":
            vid = _need(voice_id, "voice_id", op)
            return _run(lambda: _client().get_voice(vid))
        return _view(_run(lambda: _client().list_voices(
            search=search, voice_type=voice_type, category=category,
            page_size=page_size, next_page_token=next_page_token)), full, _DROP_VOICES)

    @mcp.tool(name="elevenlabs_act", annotations=ACTE)
    def elevenlabs_act(
        op: Literal["create_speech", "create_music", "create_sound", "create_voice_design"],
        text: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        voice_settings: Optional[dict] = None,
        previous_text: Optional[str] = None,
        next_text: Optional[str] = None,
        language_code: Optional[str] = None,
        timestamps: bool = False,
        prompt: Optional[str] = None,
        composition_plan: Optional[dict] = None,
        music_length_ms: Optional[int] = None,
        force_instrumental: Optional[bool] = None,
        respect_sections_durations: Optional[bool] = None,
        duration_seconds: Optional[float] = None,
        prompt_influence: Optional[float] = None,
        loop: Optional[bool] = None,
        voice_description: Optional[str] = None,
        auto_generate_text: Optional[bool] = None,
        seed: Optional[int] = None,
        output_format: Optional[str] = None,
    ) -> dict:
        """Generate audio with ElevenLabs. EVERY call spends the account's credits
        (characters for speech, seconds for music and sound) and is not retried:
        read `elevenlabs` op='get_subscription' before a batch.

        The audio comes back as a signed URL (`url`, valid `expires_in` seconds,
        deleted after a day), never inline: download it right away.

        `op`:
        - `create_speech` — `text` read by `voice_id` (from `elevenlabs`
          op='list_voices'), with `model_id`, `voice_settings`, `seed` and
          `language_code` optional. `previous_text` / `next_text` make a line sound
          continuous with its neighbours when a script is read line by line. With
          `timestamps=true` the answer also carries `words` (each word's start and
          end, seconds) and the raw per-character `alignment`.
        - `create_music` — a track from a `prompt` (length `music_length_ms`, 3,000
          to 300,000) or from a `composition_plan` (sections, each with its
          duration and positive and negative styles), never both;
          `respect_sections_durations`, `force_instrumental`, `seed` optional. A
          prompt naming an artist or a copyrighted work is refused.
        - `create_sound` — a sound effect described in `text`;
          `duration_seconds` (0.5 to 30, else the model decides),
          `prompt_influence` (0 to 1), `loop` optional.
        - `create_voice_design` — up to three previews of a voice described in
          `voice_description`, each reading `text` (or a text the model writes with
          `auto_generate_text=true`); each preview has its `generated_voice_id` and
          its own URL. Saving a preview as a voice of the account is not done here.

        `output_format` is `<codec>_<sample rate>[_<bitrate>]`, default
        `mp3_44100_128`; some (`mp3_44100_192`, `pcm_44100`) are paid plans only.

        Args:
            op: What to generate (see above).
            text: The words to read (`create_speech`), the sound to make (`create_sound`), or the preview's line (`create_voice_design`).
            voice_id: The voice that reads, for `create_speech`.
            model_id: Model id, e.g. eleven_multilingual_v2 or eleven_v3 for speech, music_v1 for music; the API's default when omitted.
            voice_settings: Speech only: {stability, similarity_boost, style, use_speaker_boost, speed}, each optional.
            previous_text: Speech only: the line before this one, for continuity.
            next_text: Speech only: the line after this one, for continuity.
            language_code: Speech only: ISO 639-1 code to force the language, when the model supports it.
            timestamps: Speech only: also return each word's start and end.
            prompt: Music only: the track described in words.
            composition_plan: Music only: {positive_global_styles, negative_global_styles, sections: [{section_name, duration_ms, positive_local_styles, negative_local_styles, lines}]}.
            music_length_ms: Music from a prompt only: the length, 3000 to 300000.
            force_instrumental: Music only: no vocals.
            respect_sections_durations: Music from a plan only: keep each section's duration exactly.
            duration_seconds: Sound only: 0.5 to 30 seconds.
            prompt_influence: Sound only: how literally to follow the text, 0 to 1.
            loop: Sound only: make it loop seamlessly.
            voice_description: Voice design only: the voice in words, e.g. age, accent, tone, pace.
            auto_generate_text: Voice design only: let the model write the preview's line.
            seed: 0 to 4294967295, to make a take reproducible as far as the model allows.
            output_format: e.g. mp3_44100_128 (default), mp3_48000_192, pcm_24000, opus_48000_64.
        """
        if op not in _ACT_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_ACT_OPS)}. "
                       "Nothing was done.")
        speech = dict(voice_id=voice_id, voice_settings=voice_settings,
                      previous_text=previous_text, next_text=next_text,
                      language_code=language_code, timestamps=timestamps)
        music = dict(prompt=prompt, composition_plan=composition_plan,
                     music_length_ms=music_length_ms, force_instrumental=force_instrumental,
                     respect_sections_durations=respect_sections_durations)
        sound = dict(duration_seconds=duration_seconds, prompt_influence=prompt_influence,
                     loop=loop)
        design = dict(voice_description=voice_description, auto_generate_text=auto_generate_text)
        sub = access.current_user_sub_or_raise()

        if op == "create_speech":
            _refuse_ignored(op, **music, **sound, **design)
            vid, line = _need(voice_id, "voice_id", op), _need(text, "text", op)
            audio = _run(lambda: _client().create_speech(
                vid, line, model_id=model_id, voice_settings=voice_settings,
                previous_text=previous_text, next_text=next_text, seed=seed,
                language_code=language_code, output_format=output_format,
                timestamps=timestamps))
            out = _deposit(audio, "speech", sub)
            if timestamps:
                out["words"] = words_of(audio.get("alignment"))
                out["alignment"] = audio.get("alignment")
            return out

        if op == "create_music":
            _refuse_ignored(op, text=text, **speech, **sound, **design)
            audio = _run(lambda: _client().create_music(
                prompt=prompt, composition_plan=composition_plan,
                music_length_ms=music_length_ms, model_id=model_id, seed=seed,
                force_instrumental=force_instrumental,
                respect_sections_durations=respect_sections_durations,
                output_format=output_format))
            return _deposit(audio, "music", sub)

        if op == "create_sound":
            _refuse_ignored(op, seed=seed, **speech, **music, **design)
            what = _need(text, "text", op)
            audio = _run(lambda: _client().create_sound(
                what, duration_seconds=duration_seconds, prompt_influence=prompt_influence,
                loop=loop, model_id=model_id, output_format=output_format))
            return _deposit(audio, "sound", sub)

        # op == "create_voice_design" — the only one left (guard above).
        _refuse_ignored(op, **speech, **music, **sound)
        described = _need(voice_description, "voice_description", op)
        result = _run(lambda: _client().create_voice_design(
            described, text=text, auto_generate_text=auto_generate_text,
            model_id=model_id, seed=seed, output_format=output_format))
        previews = []
        for i, preview in enumerate(result.get("previews") or [], start=1):
            served = _deposit(preview, f"voice-preview-{i}", sub)
            served.update(generated_voice_id=preview.get("generated_voice_id"),
                          duration_secs=preview.get("duration_secs"),
                          language=preview.get("language"))
            previews.append(served)
        return {"text": result.get("text"), "previews": previews}
