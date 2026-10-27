"""Tools `elevenlabs` / `elevenlabs_act` and the ElevenLabs probe.

What this file locks — the generic tripwires already cover registry, publisher,
logo, module derivation, served prose, the `<ns>`/`<ns>_act` convention and the
join to the client:
- the SURFACE (the pair) and the routing to the right client method, with the
  resolved key passed as is;
- audio is never inline: deposited privately, served as a signed URL;
- `timestamps` turns the per-character alignment into words;
- intent refusals (an argument of another op, a missing required argument);
- the translation of upstream refusals, and above all an EMPTY account (401
  `quota_exceeded`) told apart from a REFUSED key, by a 402 in the chain;
- the probe: 401 → `NonAutorise`, quota → `QuotaEpuise`, balance read.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.connectors import verify as cv
from oto_mcp.mcp_errors import McpError

AUDIO = b"ID3fake"


@pytest.fixture
def client(monkeypatch):
    """The fake client, the kwargs of each construction, and the deposits made."""
    inst, built, deposits = MagicMock(), [], []

    def fabrique(**kw):
        built.append(kw)
        return inst

    def upload_private(prefix, owner_id, data, content_type, filename=None, *, expiry=None):
        deposits.append((prefix, owner_id, data, content_type, filename))
        return f"https://media.test/{filename}?sig=x"

    monkeypatch.setattr("oto.tools.elevenlabs.ElevenLabsClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None, units=1: ("key-de-test", False))
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "user-sub")
    monkeypatch.setattr("oto_mcp.media_store.upload_private", upload_private)
    inst.built, inst.deposits = built, deposits
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import elevenlabs as E

    m = FastMCP("t")
    E.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status, body=None):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body or {"detail": {"status": "x", "message": "m"}},
                             service="elevenlabs")


def _audio(**extra):
    return {"content": AUDIO, "content_type": "audio/mpeg", "extension": "mp3",
            "output_format": "mp3_44100_128", **extra}


# --- surface ------------------------------------------------------------------

def test_surface_is_the_pair(client):
    tools = {t.name: t for t in asyncio.run(_mcp().list_tools())}
    assert set(tools) == {"elevenlabs", "elevenlabs_act"}
    assert tools["elevenlabs"].annotations.readOnlyHint is True
    assert tools["elevenlabs_act"].annotations.readOnlyHint is False


def test_registry_declares_one_key_byo_or_a_granted_platform_key():
    from oto_mcp import providers
    c = providers.REGISTRY["elevenlabs"]
    assert c.kind == "tools" and c.secret_kind == "api_key" and c.keyed
    assert c.auth_modes == frozenset({"byo_user", "byo_org", "platform"})
    assert c.platform_key_open is False, "the shared key needs a grant"
    assert c.default_quota == 0


def test_the_key_is_passed_as_is(client):
    client.get_subscription.return_value = {"tier": "creator"}
    assert _tool("elevenlabs")(op="get_subscription") == {"tier": "creator"}
    assert client.built == [{"api_key": "key-de-test"}]


# --- reads ----------------------------------------------------------------------

def test_list_voices_routes_its_filters(client):
    client.list_voices.return_value = {"voices": []}
    _tool("elevenlabs")(op="list_voices", search="warm", category="premade", page_size=20)
    client.list_voices.assert_called_once_with(search="warm", voice_type=None,
                                               category="premade", page_size=20,
                                               next_page_token=None)


def test_get_voice_needs_its_id(client):
    with pytest.raises(McpError, match="requires `voice_id`"):
        _tool("elevenlabs")(op="get_voice")
    client.get_voice.return_value = {"voice_id": "v1"}
    assert _tool("elevenlabs")(op="get_voice", voice_id="v1") == {"voice_id": "v1"}


def test_a_read_refuses_another_ops_argument(client):
    with pytest.raises(McpError, match="does not use `search`"):
        _tool("elevenlabs")(op="list_models", search="x")
    with pytest.raises(McpError, match="does not use `voice_id`"):
        _tool("elevenlabs")(op="list_voices", voice_id="v1")


# --- generations ------------------------------------------------------------------

def test_speech_is_served_as_a_signed_url(client):
    client.create_speech.return_value = _audio(request_id="r1", character_count=11)
    out = _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="Hello there",
                                  model_id="eleven_v3", seed=7)
    client.create_speech.assert_called_once_with(
        "v1", "Hello there", model_id="eleven_v3", voice_settings=None, previous_text=None,
        next_text=None, seed=7, language_code=None, output_format=None, timestamps=False)
    assert client.deposits == [("elevenlabs-audio", "user-sub", AUDIO, "audio/mpeg", "speech.mp3")]
    assert out["encoding"] == "url" and out["url"].startswith("https://media.test/speech.mp3")
    assert out["size"] == len(AUDIO) and out["character_count"] == 11
    assert "content" not in out and "words" not in out


def test_speech_with_timestamps_gives_words(client):
    alignment = {"characters": list("[calm] Hi there."),
                 "character_start_times_seconds": [0.0] * 7 + [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                 "character_end_times_seconds": [0.0] * 7 + [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]}
    client.create_speech.return_value = _audio(alignment=alignment)
    out = _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="[calm] Hi there.",
                                  timestamps=True)
    assert out["words"] == [{"word": "Hi", "start": 0.1, "end": 0.3},
                            {"word": "there.", "start": 0.4, "end": 1.0}]
    assert out["alignment"] == alignment


def test_music_from_a_plan(client):
    client.create_music.return_value = _audio()
    plan = {"sections": [{"section_name": "Intro", "duration_ms": 3000}]}
    out = _tool("elevenlabs_act")(op="create_music", composition_plan=plan,
                                  respect_sections_durations=True, seed=3)
    client.create_music.assert_called_once_with(
        prompt=None, composition_plan=plan, music_length_ms=None, model_id=None, seed=3,
        force_instrumental=None, respect_sections_durations=True, output_format=None)
    assert out["filename"] == "music.mp3"


def test_music_refuses_a_speech_argument(client):
    with pytest.raises(McpError, match="does not use `voice_id`"):
        _tool("elevenlabs_act")(op="create_music", prompt="calm", voice_id="v1")
    client.create_music.assert_not_called()


def test_sound(client):
    client.create_sound.return_value = _audio()
    _tool("elevenlabs_act")(op="create_sound", text="a soft click", duration_seconds=1.0)
    client.create_sound.assert_called_once_with(
        "a soft click", duration_seconds=1.0, prompt_influence=None, loop=None,
        model_id=None, output_format=None)


def test_voice_design_serves_each_preview(client):
    client.create_voice_design.return_value = {"text": "Read this.", "previews": [
        {**_audio(), "generated_voice_id": "g1", "duration_secs": 2.0, "language": "en"},
        {**_audio(), "generated_voice_id": "g2", "duration_secs": 2.1, "language": "en"}]}
    out = _tool("elevenlabs_act")(op="create_voice_design",
                                  voice_description="A calm, low narrator.", text="Read this.")
    assert [p["generated_voice_id"] for p in out["previews"]] == ["g1", "g2"]
    assert [p["filename"] for p in out["previews"]] == ["voice-preview-1.mp3", "voice-preview-2.mp3"]
    assert all("content" not in p for p in out["previews"])


def test_speech_needs_voice_and_text(client):
    with pytest.raises(McpError, match="requires `voice_id`"):
        _tool("elevenlabs_act")(op="create_speech", text="x")
    with pytest.raises(McpError, match="requires `text`"):
        _tool("elevenlabs_act")(op="create_speech", voice_id="v1")


def test_storage_down_is_said(client, monkeypatch):
    from oto_mcp import media_store

    def down(*a, **k):
        raise media_store.MediaError(503, "storage_unavailable", "no bucket")

    monkeypatch.setattr("oto_mcp.media_store.upload_private", down)
    client.create_sound.return_value = _audio()
    with pytest.raises(McpError, match="temporary storage is unavailable"):
        _tool("elevenlabs_act")(op="create_sound", text="click")


# --- upstream refusals ------------------------------------------------------------

def _chain(exc):
    seen, cur = [], exc
    while cur is not None:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return seen


def test_empty_account_is_a_402_not_a_dead_key(client):
    client.create_speech.side_effect = _upstream(401, {"detail": {"status": "quota_exceeded",
                                                                  "message": "quota"}})
    with pytest.raises(McpError, match="no credits left") as e:
        _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="x")
    assert any(getattr(x, "status_code", None) == 402 for x in _chain(e.value))


def test_restricted_key_is_not_marked_dead(client):
    client.list_voices.side_effect = _upstream(401, {"detail": {"status": "missing_permissions",
                                                                "message": "voices_read"}})
    with pytest.raises(McpError, match="restricted") as e:
        _tool("elevenlabs")(op="list_voices")
    assert not any(getattr(x, "status_code", None) == 401 for x in _chain(e.value))


def test_refused_key_keeps_its_401_in_the_chain(client):
    client.get_subscription.side_effect = _upstream(401, {"detail": {"status": "invalid_api_key"}})
    with pytest.raises(McpError, match="refused the API key") as e:
        _tool("elevenlabs")(op="get_subscription")
    assert any(getattr(x, "status_code", None) == 401 for x in _chain(e.value))


def test_local_refusal_of_the_client_is_served(client):
    client.create_music.side_effect = ValueError("Give either `prompt` or `composition_plan`")
    with pytest.raises(McpError, match="Give either"):
        _tool("elevenlabs_act")(op="create_music")


# --- probe ------------------------------------------------------------------------

def _probe_with(monkeypatch, behaviour):
    inst = MagicMock()
    inst.probe.side_effect = behaviour if isinstance(behaviour, Exception) else None
    if not isinstance(behaviour, Exception):
        inst.probe.return_value = behaviour
    monkeypatch.setattr("oto.tools.elevenlabs.ElevenLabsClient", lambda **kw: inst)
    from oto_mcp.tools import elevenlabs as E
    return E._verify


def test_probe_ok(monkeypatch):
    verify = _probe_with(monkeypatch, {"tier": "creator", "character_count": 10,
                                       "character_limit": 100})
    verify({"key": "k"})


def test_probe_refused_key(monkeypatch):
    verify = _probe_with(monkeypatch, _upstream(401, {"detail": {"status": "invalid_api_key"}}))
    with pytest.raises(cv.NonAutorise):
        verify({"key": "k"})


def test_probe_empty_account(monkeypatch):
    verify = _probe_with(monkeypatch, {"tier": "creator", "character_count": 100,
                                       "character_limit": 100})
    with pytest.raises(cv.QuotaEpuise):
        verify({"key": "k"})
    verify = _probe_with(monkeypatch, _upstream(401, {"detail": {"status": "quota_exceeded"}}))
    with pytest.raises(cv.QuotaEpuise):
        verify({"key": "k"})


def test_probe_coverage_is_auth_and_quota(client):
    _mcp()
    assert cv.couverture("elevenlabs") == cv.AUTH_QUOTA


# --- tightened lists ----------------------------------------------------------------

def test_voice_list_is_tightened_by_default_and_says_so(client):
    raw = {"voices": [{"voice_id": "v1", "name": "A", "samples": [{"x": 1}],
                       "fine_tuning": {"state": {}}, "labels": {"accent": "us"}}],
           "has_more": False}
    client.list_voices.return_value = raw
    out = _tool("elevenlabs")(op="list_voices")
    assert out["voices"] == [{"voice_id": "v1", "name": "A", "labels": {"accent": "us"}}]
    assert out["projection"]["omitted"] == ["fine_tuning", "samples"]
    assert _tool("elevenlabs")(op="list_voices", full=True) == raw


def test_model_list_is_a_list_and_stays_one_under_projection(client):
    client.list_models.return_value = [{"model_id": "m1", "languages": [{"x": 1}]}]
    out = _tool("elevenlabs")(op="list_models")
    assert out == {"items": [{"model_id": "m1"}],
                   "projection": {"omitted": ["languages"], "hint": "full=true returns the raw records."}}


def test_full_belongs_to_the_lists(client):
    with pytest.raises(McpError, match="does not use `full`"):
        _tool("elevenlabs")(op="get_subscription", full=True)


# --- the platform key: speech by default, metered in ElevenLabs credits --------------

@pytest.fixture
def platform(monkeypatch, client):
    """The shared key wins the cascade; `quota` = the `units` the resolver was asked to
    cover."""
    quota: list = []

    def resolve(provider, account=None, units=1):
        quota.append(units)
        return ("platform-key", True)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", resolve)
    monkeypatch.setattr("oto_mcp.auth.hooks.current_user_sub_from_token", lambda: "user-sub")
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.links.ou_poser_la_cle", lambda *a, **k: "")
    return quota


@pytest.fixture
def trace(monkeypatch):
    seen: list = []
    monkeypatch.setattr("oto_mcp.session_org.note_call_trace", lambda **kw: seen.append(kw))
    return seen


@pytest.fixture
def setting(monkeypatch):
    """The `platform.ops` row of `connector_settings`: None = unset."""
    row = {"value": None}

    def _get(scope_type, scope_id, connector, key, *a, **kw):
        assert (scope_type, scope_id, connector, key) == (
            "platform", "platform", "elevenlabs", "platform.ops")
        return row["value"]
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting", _get)
    return row


@pytest.mark.parametrize("model_id, chars, credits", [
    ("eleven_flash_v2_5", 11, 6),          # half rate, rounded UP to a whole credit
    ("eleven_turbo_v2", 10, 5),
    ("eleven_flash_v2", 1, 1),
    ("eleven_turbo_v2_5", 0, 0),
    ("eleven_multilingual_v2", 11, 11),
    ("eleven_v3", 11, 11),
    (None, 11, 11),                        # the API's default model: full rate
    ("some_future_model", 7, 7),
])
def test_speech_credits(model_id, chars, credits):
    from oto_mcp.tools import elevenlabs as E
    assert E.speech_credits("x" * 99, model_id, chars) == credits
    assert isinstance(E.speech_credits("x", model_id, chars), int)


def test_speech_credits_fall_back_on_the_texts_length():
    from oto_mcp.tools import elevenlabs as E
    assert E.speech_credits("Hello there", "eleven_flash_v2_5") == 6
    assert E.speech_credits("Hello there", None, None) == 11


def test_platform_speech_runs_and_notes_credits_both_axes(client, platform, trace, setting):
    client.create_speech.return_value = _audio(character_count=11)
    out = _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="Hello there",
                                  model_id="eleven_flash_v2_5")
    assert out["url"].startswith("https://media.test/")
    assert client.built == [{"api_key": "platform-key"}]
    assert platform == [6], "the grant's quota is checked for the call's credits"
    assert trace == [{"units": 6, "quantity": 6}]


def test_platform_speech_without_the_header_counts_the_text(client, platform, trace, setting):
    client.create_speech.return_value = _audio()
    _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="Hello there")
    assert trace == [{"units": 11, "quantity": 11}]


@pytest.mark.parametrize("kwargs", [
    {"op": "create_music", "prompt": "calm piano"},
    {"op": "create_sound", "text": "a soft click"},
    {"op": "create_voice_design", "voice_description": "calm", "text": "Hi."},
])
def test_unset_the_platform_key_refuses_every_other_generation(
        client, platform, trace, setting, kwargs):
    with pytest.raises(McpError, match="needs your own ElevenLabs key"):
        _tool("elevenlabs_act")(**kwargs)
    assert not client.method_calls, "a refused op must not reach ElevenLabs (no credit spent)"
    assert trace == []


def test_the_setting_opens_another_generation(client, platform, trace, setting):
    setting["value"] = "elevenlabs_act:create_sound"
    client.create_sound.return_value = _audio(character_count=40)
    _tool("elevenlabs_act")(op="create_sound", text="a soft click")
    assert trace == [{"units": 40, "quantity": 40}]
    with pytest.raises(McpError, match="needs your own ElevenLabs key"):
        _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="Hi")
    setting["value"] = "elevenlabs_act"
    client.create_music.return_value = _audio(character_count=500)
    _tool("elevenlabs_act")(op="create_music", prompt="calm piano")
    assert trace[-1] == {"units": 500, "quantity": 500}


def test_platform_reads_are_free_but_the_balance_is_never_served(
        client, platform, trace, setting):
    client.list_voices.return_value = {"voices": []}
    _tool("elevenlabs")(op="list_voices")
    assert trace == [{"units": 0}], "a read: no quantity, 0 units"
    with pytest.raises(McpError, match="needs your own ElevenLabs key"):
        _tool("elevenlabs")(op="get_subscription")
    client.get_subscription.assert_not_called()


def test_own_key_notes_the_quantity_only_and_ignores_the_setting(client, trace, setting):
    setting["value"] = "elevenlabs_act:create_sound"
    client.create_speech.return_value = _audio(character_count=10)
    _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="0123456789")
    assert trace == [{"quantity": 10}]
    client.list_voices.return_value = {"voices": []}
    _tool("elevenlabs")(op="list_voices")
    assert trace == [{"quantity": 10}], "a read on the own key notes nothing"


def test_a_failed_generation_notes_nothing(client, platform, trace, setting):
    client.create_speech.side_effect = _upstream(500)
    with pytest.raises(McpError):
        _tool("elevenlabs_act")(op="create_speech", voice_id="v1", text="Hi")
    assert trace == []


@pytest.mark.parametrize("value, error", [
    ("elevenlabs", "not an ElevenLabs tool"),
    ("elevenlabs_act:nope", "has no op"),
    ("forager_person", "not an ElevenLabs tool"),
])
def test_the_setting_refuses_what_the_tool_would_not_understand(value, error):
    from oto_mcp.tools import elevenlabs as E
    with pytest.raises(ValueError, match=error):
        E.parse_platform_ops(value)


def test_the_default_is_speech_only():
    from oto_mcp.tools import elevenlabs as E
    assert E.parse_platform_ops(E.PLATFORM_OPS_DEFAULT) == {("elevenlabs_act", "create_speech")}


def test_an_unreadable_setting_RAISES_rather_than_serving_the_default(monkeypatch):
    from oto_mcp.tools import elevenlabs as E
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **kw: "elevenlabs_act:gone")
    with pytest.raises(RuntimeError, match="not understood"):
        E.platform_ops()
