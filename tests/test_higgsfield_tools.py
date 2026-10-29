"""Tools `higgsfield` / `higgsfield_act` and the Higgsfield probe.

What this file locks — the generic tripwires already cover registry, publisher,
logo, module derivation, served prose, the `<ns>`/`<ns>_act` convention and the
join to the client:
- the SURFACE (the pair) and the routing to the right client method, with the key
  passed as is;
- the PRICE GUARD: `create_generation` estimates first and submits only a job whose price is
  read and at most `max_usd`; an unreadable price or one above the cap sends nothing;
- the instance's CEILING on `max_usd` (`OTO_HIGGSFIELD_MAX_USD_PAR_APPEL`): above it,
  `max_usd_above_cap` before Higgsfield is called; an unreadable setting raises;
- an EMPTY balance (403 on a generation) told apart from a refused key, by a 402 in
  the chain; a refused key keeps its 401;
- intent refusals (an argument of another op, a missing required argument);
- the tightened model list and its filters; the flat `outputs` of a completed job;
- the probe: 401/403 → `NonAutorise`, any other code unclassified.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.connectors import verify as cv
from oto_mcp.mcp_errors import McpError

_KEY = "id-de-test:secret-de-test"
_RID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
_SEEDANCE = {"type": "description", "pricing_description": (
    "For 16:9 video without video input, your request costs roughly $0.2056 per second of "
    "generated video at 480p, $0.4622 at 720p, and $1.1372 at 1080p. Each 1,000 video tokens "
    "costs $0.0214 at 480p or 720p and $0.0234 at 1080p.")}
_MODELS = {"total": 3, "items": [
    {"slug": "higgsfield-ai/soul/v2/standard", "title": "Soul 2", "description": "Stills",
     "output_type": "image", "operation_type": ["text2image"], "base_credits": "0.0000",
     "is_workflow": False, "fnf_job_set_type": "generic_image", "input_schema": None},
    {"slug": "kling-video/v3.0/pro/image-to-video", "title": "Kling 3.0 Pro", "description": "Clips",
     "output_type": "video", "operation_type": ["image2video"], "base_credits": "0.0000",
     "is_workflow": False, "fnf_job_set_type": "generic_video", "input_schema": None},
    {"slug": "kling-video/v3.0/pro/text-to-video", "title": "Kling 3.0 Pro", "description": "Clips",
     "output_type": "video", "operation_type": ["text2video"], "base_credits": "0.0000",
     "is_workflow": False, "fnf_job_set_type": "generic_video", "input_schema": None},
]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OTO_HIGGSFIELD_MAX_USD_PAR_APPEL", raising=False)
    inst, built = MagicMock(), []

    def fabrique(**kw):
        built.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.higgsfield.HiggsfieldClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None, units=1: (_KEY, False))
    inst.built = built
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import higgsfield as H

    m = FastMCP("t")
    H.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status, body=None):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body or {"detail": "x"}, service="higgsfield")


def _chain(exc):
    seen = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


# --- surface ------------------------------------------------------------------

def test_surface_is_the_pair(client):
    tools = {t.name: t for t in asyncio.run(_mcp().list_tools())}
    assert set(tools) == {"higgsfield", "higgsfield_act"}
    assert tools["higgsfield"].annotations.readOnlyHint is True
    assert tools["higgsfield_act"].annotations.readOnlyHint is False


def test_registry_declares_one_secret_key_byok_only():
    from oto_mcp import providers
    c = providers.REGISTRY["higgsfield"]
    assert c.kind == "tools" and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert [(f.name, f.secret) for f in c.credential_fields] == [("key", True)]


def test_the_key_is_passed_as_is(client):
    client.list_models.return_value = _MODELS
    _tool("higgsfield")(op="list_models")
    assert client.built == [{"api_key": _KEY}]


# --- reads ----------------------------------------------------------------------

def test_model_list_is_tightened_by_default_and_says_so(client):
    client.list_models.return_value = _MODELS
    out = _tool("higgsfield")(op="list_models")
    assert out["total"] == 3
    assert set(out["items"][0]) == {"slug", "title", "description", "output_type", "operation_type"}
    assert out["projection"]["omitted"] == ["base_credits", "fnf_job_set_type", "input_schema", "is_workflow"]
    raw = _tool("higgsfield")(op="list_models", full=True)
    assert raw["items"][0]["input_schema"] is None and "projection" not in raw


@pytest.mark.parametrize("kwargs,slugs", [
    ({"output_type": "image"}, ["higgsfield-ai/soul/v2/standard"]),
    ({"operation_type": "image2video"}, ["kling-video/v3.0/pro/image-to-video"]),
    ({"search": "KLING text"}, ["kling-video/v3.0/pro/text-to-video"]),
    ({"output_type": "video", "search": "soul"}, []),
])
def test_model_list_filters(client, kwargs, slugs):
    client.list_models.return_value = _MODELS
    out = _tool("higgsfield")(op="list_models", **kwargs)
    assert [m["slug"] for m in out["items"]] == slugs and out["total"] == len(slugs)


def test_estimate_reads_a_price(client):
    client.estimate.return_value = {"type": "estimate", "usd": "0.004", "credits": "0.050"}
    out = _tool("higgsfield")(op="estimate", model="higgsfield-ai/soul/v2/standard",
                              params={"prompt": "a lamp"})
    client.estimate.assert_called_once_with("higgsfield-ai/soul/v2/standard", {"prompt": "a lamp"})
    assert out["usd"] == 0.004 and out["approximate"] is False
    assert out["estimate"]["credits"] == "0.050"


def test_get_request_lists_the_outputs_once_completed(client):
    client.get_request.return_value = {"status": "completed", "request_id": _RID,
                                       "images": [{"url": "https://cdn.test/1.png"},
                                                  {"url": "https://cdn.test/2.png"}],
                                       "expires_at": "2026-11-04T12:00:00Z"}
    out = _tool("higgsfield")(op="get_request", request_id=_RID)
    assert out["outputs"] == ["https://cdn.test/1.png", "https://cdn.test/2.png"]
    client.get_request.return_value = {"status": "in_progress", "request_id": _RID}
    assert "outputs" not in _tool("higgsfield")(op="get_request", request_id=_RID)


def test_outputs_of_a_video_and_an_audio():
    from oto_mcp.tools.higgsfield import outputs_of
    assert outputs_of({"video": {"url": "https://cdn.test/v.mp4"}}) == ["https://cdn.test/v.mp4"]
    assert outputs_of({"audio": {"url": "https://cdn.test/a.mp3"},
                       "audios": [{"url": "https://cdn.test/a.mp3"}]}) == ["https://cdn.test/a.mp3"]
    assert outputs_of(None) == []


def test_read_refusals(client):
    with pytest.raises(McpError, match="requires `request_id`"):
        _tool("higgsfield")(op="get_request")
    with pytest.raises(McpError, match="requires `params`"):
        _tool("higgsfield")(op="estimate", model="z-image/turbo")
    with pytest.raises(McpError, match="does not use `full`"):
        _tool("higgsfield")(op="get_request", request_id=_RID, full=True)
    with pytest.raises(McpError, match="does not use `model`"):
        _tool("higgsfield")(op="list_models", model="z-image/turbo")


# --- the price guard --------------------------------------------------------------

def test_generate_estimates_then_submits_under_the_cap(client):
    client.estimate.return_value = {"usd": "0.480"}
    client.generate.return_value = {"status": "queued", "request_id": _RID}
    params = {"image_url": "https://cdn.test/a.png", "prompt": "push in", "duration": 5, "sound": "off"}
    out = _tool("higgsfield_act")(op="create_generation", model="kling-video/v3.0/pro/image-to-video",
                                  params=params, max_usd=0.5, idempotency_key="take-1")
    client.estimate.assert_called_once_with("kling-video/v3.0/pro/image-to-video", params)
    client.generate.assert_called_once_with("kling-video/v3.0/pro/image-to-video", params,
                                            idempotency_key="take-1", expires_after=None)
    assert out["request_id"] == _RID
    assert out["price"] == {"usd": 0.48, "approximate": False, "basis": "the estimate's usd"}


def test_generate_above_the_cap_sends_nothing(client):
    client.estimate.return_value = _SEEDANCE
    with pytest.raises(McpError, match=r"about \$2\.311 .* above max_usd \$1\.50. Nothing was sent"):
        _tool("higgsfield_act")(op="create_generation", model="bytedance/seedance-2.5/image-to-video",
                                params={"image_url": "https://cdn.test/a.png", "duration": 5,
                                        "resolution": "720p"}, max_usd=1.5)
    client.generate.assert_not_called()


@pytest.mark.parametrize("estimate,params,needle", [
    (_SEEDANCE, {"resolution": "720p"}, "duration"),
    (_SEEDANCE, {"duration": 5, "video_url": "https://cdn.test/in.mp4"}, "video"),
    ({"type": "description", "pricing_description": "Contact us"}, {}, "names no rate"),
    ({}, {}, "gives no amount"),
])
def test_generate_with_an_unreadable_price_sends_nothing(client, estimate, params, needle):
    client.estimate.return_value = estimate
    with pytest.raises(McpError, match=f"cannot be read .*{needle}.*not sent"):
        _tool("higgsfield_act")(op="create_generation", model="bytedance/seedance-2.5/video-edit",
                                params=params, max_usd=5)
    client.generate.assert_not_called()


@pytest.mark.parametrize("max_usd", [None, 0, -1, True])
def test_generate_needs_a_positive_cap(client, max_usd):
    with pytest.raises(McpError, match="max_usd"):
        _tool("higgsfield_act")(op="create_generation", model="z-image/turbo", params={"prompt": "x"},
                                max_usd=max_usd)
    client.estimate.assert_not_called()
    client.generate.assert_not_called()


@pytest.mark.parametrize("setting,max_usd", [(None, 5.01), (None, 500), ("2", 2.5)])
def test_max_usd_above_the_instance_ceiling_is_refused_before_any_call(
        client, monkeypatch, setting, max_usd):
    from oto_mcp.tools.higgsfield import MAX_USD_ABOVE_CAP
    if setting is not None:
        monkeypatch.setenv("OTO_HIGGSFIELD_MAX_USD_PAR_APPEL", setting)
    with pytest.raises(McpError) as e:
        _tool("higgsfield_act")(op="create_generation", model="z-image/turbo",
                                params={"prompt": "x"}, max_usd=max_usd)
    assert e.value.error.message.startswith(f"{MAX_USD_ABOVE_CAP}:")
    client.estimate.assert_not_called()
    client.generate.assert_not_called()


def test_a_raised_ceiling_lets_a_dearer_job_through(client, monkeypatch):
    monkeypatch.setenv("OTO_HIGGSFIELD_MAX_USD_PAR_APPEL", "20")
    client.estimate.return_value = {"usd": "12.5"}
    client.generate.return_value = {"status": "queued", "request_id": _RID}
    out = _tool("higgsfield_act")(op="create_generation", model="z-image/turbo",
                                  params={"prompt": "x"}, max_usd=15)
    assert out["request_id"] == _RID


@pytest.mark.parametrize("setting", ["", "cinq", "0", "-1", "nan", "inf"])
def test_an_unreadable_ceiling_raises_and_never_falls_back(client, monkeypatch, setting):
    monkeypatch.setenv("OTO_HIGGSFIELD_MAX_USD_PAR_APPEL", setting)
    with pytest.raises(RuntimeError, match="OTO_HIGGSFIELD_MAX_USD_PAR_APPEL"):
        _tool("higgsfield_act")(op="create_generation", model="z-image/turbo",
                                params={"prompt": "x"}, max_usd=1)
    client.estimate.assert_not_called()
    client.generate.assert_not_called()


def test_an_estimate_refusal_stops_before_any_submission(client):
    client.estimate.side_effect = _upstream(400, {"detail": ": 'image_url' is a required property"})
    with pytest.raises(McpError, match="image_url"):
        _tool("higgsfield_act")(op="create_generation", model="kling-video/v3.0/pro/image-to-video",
                                params={"prompt": "x"}, max_usd=1)
    client.generate.assert_not_called()


# --- empty balance vs refused key ------------------------------------------------

def test_empty_balance_is_a_402_not_a_dead_key(client):
    from oto_mcp.error_taxonomy import classify, credential_rejected_in_chain
    client.estimate.return_value = {"usd": "0.004"}
    client.generate.side_effect = _upstream(403, {"detail": "Insufficient balance"})
    with pytest.raises(McpError, match="balance is too low") as e:
        _tool("higgsfield_act")(op="create_generation", model="z-image/turbo", params={"prompt": "x"},
                                max_usd=0.01)
    assert any(getattr(x, "status_code", None) == 402 for x in _chain(e.value))
    assert classify(e.value).code == "quota_exhausted"
    assert credential_rejected_in_chain(e.value) is False


def test_refused_key_keeps_its_401_in_the_chain(client):
    from oto_mcp.error_taxonomy import credential_rejected_in_chain
    client.list_models.side_effect = _upstream(401, {"detail": "Invalid credentials"})
    with pytest.raises(McpError, match="KEY_ID:KEY_SECRET") as e:
        _tool("higgsfield")(op="list_models")
    assert credential_rejected_in_chain(e.value) is True


@pytest.mark.parametrize("status,body,needle", [
    (404, {"detail": "model_not_found"}, "model_not_found"),
    (409, {"detail": "running"}, "still queued or in progress"),
    (422, {"detail": [{"msg": "idempotency key reused"}]}, "idempotency key reused"),
    (423, {"detail": "blocked"}, "temporarily blocked"),
    (429, {"detail": {"code": "account_queue_full", "message": "Too many queued generations"}},
     "Too many queued generations"),
    (503, {"detail": {"code": "admission_control_unavailable"}}, "temporarily unavailable"),
])
def test_upstream_refusals_translated(client, status, body, needle):
    client.delete_request.side_effect = _upstream(status, body)
    with pytest.raises(McpError, match=needle):
        _tool("higgsfield_act")(op="delete", request_id=_RID)


# --- other acts -------------------------------------------------------------------

def test_cancel_delete_and_upload_route_to_the_client(client):
    client.cancel_request.return_value = {"request_id": _RID, "status": "canceled"}
    client.delete_request.return_value = {"request_id": _RID, "status": "deleted"}
    client.create_upload_url.return_value = {"upload_url": "https://s.test/put", "public_url": "https://cdn.test/a"}
    assert _tool("higgsfield_act")(op="cancel", request_id=_RID)["status"] == "canceled"
    assert _tool("higgsfield_act")(op="delete", request_id=_RID)["status"] == "deleted"
    assert _tool("higgsfield_act")(op="create_upload", content_type="image/png")["public_url"]
    client.cancel_request.assert_called_once_with(_RID)
    client.delete_request.assert_called_once_with(_RID)
    client.create_upload_url.assert_called_once_with("image/png")


def test_act_refusals(client):
    with pytest.raises(McpError, match="does not use `max_usd`"):
        _tool("higgsfield_act")(op="cancel", request_id=_RID, max_usd=1)
    with pytest.raises(McpError, match="does not use `request_id`"):
        _tool("higgsfield_act")(op="create_upload", content_type="image/png", request_id=_RID)
    with pytest.raises(McpError, match="does not use `content_type`"):
        _tool("higgsfield_act")(op="delete", request_id=_RID, content_type="image/png")
    with pytest.raises(McpError, match="requires `model`"):
        _tool("higgsfield_act")(op="create_generation", params={}, max_usd=1)
    client.generate.assert_not_called()
    client.delete_request.assert_not_called()


# --- probe ------------------------------------------------------------------------

def _probe_with(monkeypatch, error=None):
    inst = MagicMock()
    if error is not None:
        inst.probe.side_effect = error
    monkeypatch.setattr("oto.tools.higgsfield.HiggsfieldClient", lambda **kw: inst)
    from oto_mcp.tools import higgsfield as H
    return H._verify


def test_probe_ok(monkeypatch):
    _probe_with(monkeypatch)({"key": _KEY})


@pytest.mark.parametrize("status", [401, 403])
def test_probe_refused(monkeypatch, status):
    with pytest.raises(cv.NonAutorise):
        _probe_with(monkeypatch, _upstream(status))({"key": _KEY})


def test_probe_other_error_unclassified(monkeypatch):
    with pytest.raises(RuntimeError):
        _probe_with(monkeypatch, _upstream(500))({"key": _KEY})


def test_probe_malformed_key_is_refused_before_any_call():
    from oto_mcp.tools import higgsfield as H
    with pytest.raises(cv.NonAutorise, match="KEY_ID:KEY_SECRET"):
        H._verify({"key": "no-colon-here"})
