"""Tools `mux` / `mux_act` and the Mux probe.

What this file locks — the generic tripwires already cover registry, publisher,
logo, module derivation, served prose, the `<ns>`/`<ns>_act` convention and the
join to the client:
- the SURFACE (the pair) and the routing to the right client method, with the
  two-field credential passed as is;
- `stream_url` added beside every PUBLIC playback id, never a signed one;
- intent refusals (an argument of another op, a missing required argument);
- the translation of upstream refusals;
- the probe: 401/403 → `NonAutorise`, any other code unclassified.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.connectors import verify as cv
from oto_mcp.mcp_errors import McpError

_CREDS = {"token_id": "id-de-test", "token_secret": "secret-de-test"}


@pytest.fixture
def client(monkeypatch):
    inst, built = MagicMock(), []

    def fabrique(**kw):
        built.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.mux.MuxClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, account=None: dict(_CREDS))
    inst.built = built
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import mux as M

    m = FastMCP("t")
    M.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status, body=None):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body or {"error": {"type": "x"}}, service="mux")


_ASSET = {"data": {"id": "a1", "status": "ready", "playback_ids": [
    {"id": "pubid", "policy": "public"}, {"id": "sigid", "policy": "signed"}]}}


# --- surface ------------------------------------------------------------------

def test_surface_is_the_pair(client):
    tools = {t.name: t for t in asyncio.run(_mcp().list_tools())}
    assert set(tools) == {"mux", "mux_act"}
    assert tools["mux"].annotations.readOnlyHint is True
    assert tools["mux_act"].annotations.readOnlyHint is False


def test_registry_declares_two_fields_one_secret():
    from oto_mcp import providers
    c = providers.REGISTRY["mux"]
    assert c.kind == "tools" and c.secret_kind == "fields"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert {(f.name, f.secret) for f in c.credential_fields} == {
        ("token_id", False), ("token_secret", True)}


def test_the_credential_is_passed_as_is(client):
    client.list_assets.return_value = {"data": []}
    _tool("mux")(op="list_assets")
    assert client.built == [_CREDS]


# --- reads ----------------------------------------------------------------------

def test_get_asset_adds_stream_url_to_public_ids_only(client):
    client.get_asset.return_value = {"data": {**_ASSET["data"], "playback_ids": [
        dict(p) for p in _ASSET["data"]["playback_ids"]]}}
    out = _tool("mux")(op="get_asset", asset_id="a1")
    ids = {p["id"]: p for p in out["data"]["playback_ids"]}
    assert ids["pubid"]["stream_url"] == "https://stream.mux.com/pubid.m3u8"
    assert "stream_url" not in ids["sigid"]


def test_list_assets_routes_paging_and_upload_filter(client):
    client.list_assets.return_value = {"data": [], "next_cursor": None}
    _tool("mux")(op="list_assets", limit=5, cursor="c", upload_id="u1")
    client.list_assets.assert_called_once_with(limit=5, cursor="c", upload_id="u1")


def test_get_ingest_reads_the_upload(client):
    client.get_upload.return_value = {"data": {"status": "asset_created", "asset_id": "a1"}}
    assert _tool("mux")(op="get_ingest", upload_id="u1")["data"]["asset_id"] == "a1"


def test_read_refusals(client):
    with pytest.raises(McpError, match="requires `asset_id`"):
        _tool("mux")(op="get_asset")
    with pytest.raises(McpError, match="does not use `limit`"):
        _tool("mux")(op="get_ingest", upload_id="u1", limit=3)
    with pytest.raises(McpError, match="does not use `asset_id`"):
        _tool("mux")(op="list_assets", asset_id="a1")


# --- writes ---------------------------------------------------------------------

def test_create_upload(client):
    client.create_upload.return_value = {"data": {"id": "u1", "url": "https://storage.test/put"}}
    out = _tool("mux_act")(op="create_upload", title="A film", max_resolution_tier="1440p",
                           video_quality="plus")
    client.create_upload.assert_called_once_with(
        cors_origin=None, timeout=None, playback_policy=None, video_quality="plus",
        max_resolution_tier="1440p", passthrough=None, title="A film", test=None)
    assert out["data"]["url"] == "https://storage.test/put"


def test_create_asset_from_an_address(client):
    client.create_asset.return_value = {"data": {"id": "a1", "status": "preparing",
                                                 "playback_ids": [{"id": "p", "policy": "public"}]}}
    out = _tool("mux_act")(op="create_asset", input_url="https://files.test/f.mp4", test=True)
    client.create_asset.assert_called_once_with(
        "https://files.test/f.mp4", playback_policy=None, video_quality=None,
        max_resolution_tier=None, passthrough=None, title=None, test=True)
    assert out["data"]["playback_ids"][0]["stream_url"] == "https://stream.mux.com/p.m3u8"


def test_write_refusals(client):
    with pytest.raises(McpError, match="requires `input_url`"):
        _tool("mux_act")(op="create_asset")
    with pytest.raises(McpError, match="does not use `input_url`"):
        _tool("mux_act")(op="create_upload", input_url="https://x.test/a.mp4")
    with pytest.raises(McpError, match="does not use `timeout`"):
        _tool("mux_act")(op="create_asset", input_url="https://x.test/a.mp4", timeout=60)
    client.create_upload.assert_not_called()
    client.create_asset.assert_not_called()


# --- upstream refusals ------------------------------------------------------------

@pytest.mark.parametrize("status,needle", [
    (401, "refused the access token"), (403, "lacks write access"),
    (404, "ONE environment"), (429, "rate limit"), (503, "temporarily unavailable"),
])
def test_upstream_refusals_translated(client, status, needle):
    client.get_asset.side_effect = _upstream(status)
    with pytest.raises(McpError, match=needle):
        _tool("mux")(op="get_asset", asset_id="a1")


# --- probe ------------------------------------------------------------------------

def _probe_with(monkeypatch, error=None):
    inst = MagicMock()
    if error is not None:
        inst.probe.side_effect = error
    monkeypatch.setattr("oto.tools.mux.MuxClient", lambda **kw: inst)
    from oto_mcp.tools import mux as M
    return M._verify


def test_probe_ok(monkeypatch):
    _probe_with(monkeypatch)(dict(_CREDS))


@pytest.mark.parametrize("status", [401, 403])
def test_probe_refused(monkeypatch, status):
    with pytest.raises(cv.NonAutorise):
        _probe_with(monkeypatch, _upstream(status))(dict(_CREDS))


def test_probe_other_error_unclassified(monkeypatch):
    with pytest.raises(RuntimeError):
        _probe_with(monkeypatch, _upstream(500))(dict(_CREDS))


# --- tightened list -----------------------------------------------------------------

def test_asset_list_is_tightened_by_default_and_says_so(client):
    raw = {"data": [{"id": "a1", "status": "ready", "duration": 56.0,
                     "tracks": [{"type": "video"}], "master_access": "none",
                     "playback_ids": [{"id": "p", "policy": "public"}]}]}
    client.list_assets.return_value = raw
    out = _tool("mux")(op="list_assets")
    assert out["data"] == [{"id": "a1", "status": "ready", "duration": 56.0,
                            "playback_ids": [{"id": "p", "policy": "public",
                                              "stream_url": "https://stream.mux.com/p.m3u8"}]}]
    assert out["projection"]["omitted"] == ["master_access", "tracks"]
    client.list_assets.return_value = {"data": [{"id": "a1", "tracks": [{"type": "video"}]}]}
    assert _tool("mux")(op="list_assets", full=True)["data"][0]["tracks"] == [{"type": "video"}]


def test_full_belongs_to_the_list(client):
    with pytest.raises(McpError, match="does not use `full`"):
        _tool("mux")(op="get_asset", asset_id="a1", full=True)
