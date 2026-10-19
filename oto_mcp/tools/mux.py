"""Mux Video — the videos (assets) of an environment, and getting a file in.

Wraps `oto.tools.mux.MuxClient` (Basic `token_id:token_secret`), credential
resolved per call via `access.resolve_credential_fields("mux")` (BYOK, user or
org).

**Surface** (the `<ns>` / `<ns>_act` pair):
- `mux` (read-only) — list the assets, one asset (status, duration, playback
  ids), one direct upload (`get_ingest`: has the file arrived, which asset it made);
- `mux_act` — make a direct upload address (the file is then PUT there by
  whoever holds it: the bytes never pass through here), or an asset from a
  file Mux fetches at an https address.

Calls to the client are written out in full (`_client().get_asset(…)`): this is
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

_NAME = "mux"

_READ_OPS = ("list_assets", "get_asset", "get_ingest")
_ACT_OPS = ("create_upload", "create_asset")

STREAM = "https://stream.mux.com/{}.m3u8"

# Keys removed from the asset LIST by default, at any depth: the per-track details,
# the rendition and master-file settings, the encoder's input diagnostics.
# `get_asset` keeps everything.
_DROP_ASSETS = frozenset({
    "tracks", "master_access", "mp4_support", "static_renditions", "encoding_tier",
    "non_standard_input_reasons", "ingest_type", "recording_times", "source_asset_id",
    "master",
})
_HINT = "full=true returns the raw records."


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


def _upstream_message(e) -> str:
    status = e.status_code
    detail = str(e.body)[:400]
    if status == 401:
        return ("Mux refused the access token (HTTP 401): check its ID and secret key, "
                "and that it was created with the Mux Video permission.")
    if status == 403:
        return ("Mux refused the operation (HTTP 403): the token lacks write access to "
                f"Mux Video, or the plan does not include it. {detail}")
    if status == 404:
        return ("Mux: not found (404). An asset or upload belongs to ONE environment: "
                "check the token is the one of the environment that holds it.")
    if status == 400:
        return f"Mux rejected the request (HTTP 400): {detail}"
    if status == 429:
        return "Mux: rate limit reached (429). Retry in a moment."
    if status >= 500:
        return f"Mux is temporarily unavailable (HTTP {status}); retry later."
    return f"Mux refused the request (HTTP {status}): {detail}"


def _refuse_ignored(op: str, **provided) -> None:
    """An argument supplied that THIS op does not use is an intent error."""
    for name, value in provided.items():
        if value is not None and value is not False:
            raise _bad(f"op='{op}' does not use `{name}`. Nothing was done.")


def _need(value, name: str, op: str):
    if value is None or value == "":
        raise _bad(f"op='{op}' requires `{name}`.")
    return value


def with_stream_urls(payload: Any) -> Any:
    """Add `stream_url` beside every PUBLIC playback id of an asset (or of each
    asset of a list): the HLS address a player takes."""
    data = payload.get("data") if isinstance(payload, dict) else None
    for asset in data if isinstance(data, list) else [data]:
        if not isinstance(asset, dict):
            continue
        for pid in asset.get("playback_ids") or []:
            if isinstance(pid, dict) and pid.get("policy") == "public" and pid.get("id"):
                pid["stream_url"] = STREAM.format(pid["id"])
    return payload


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Probe for "test the connection": one asset of the environment, the cheapest
    authenticated read. Mux bills usage after the fact: there is no balance to read."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.mux import MuxClient

    try:
        MuxClient(token_id=fields.get("token_id"),
                  token_secret=fields.get("token_secret")).probe()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Mux HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Mux HTTP {e.status_code}: {e.body}")
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.mux import MuxClient

    connector_verify.register(_NAME, _verify)

    def _client() -> MuxClient:
        creds = access.resolve_credential_fields(_NAME)
        return MuxClient(token_id=creds.get("token_id"),
                         token_secret=creds.get("token_secret"))

    def _run(fn):
        try:
            return fn()
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except ValueError as e:
            raise _bad(str(e))

    @mcp.tool(name="mux", annotations=LECTURE)
    def mux(
        op: Literal["list_assets", "get_asset", "get_ingest"],
        asset_id: Optional[str] = None,
        upload_id: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        full: bool = False,
    ) -> Any:
        """Mux Video, read-only: the videos (assets) of the token's environment, and
        the direct uploads that make them.

        `op`:
        - `list_assets` — newest first: id, `status`, `duration` (seconds),
          resolution, playback ids, `passthrough`, title. `limit` 1-100 (default
          25), `next_cursor` in the answer goes into `cursor`. With `upload_id`:
          the asset that upload made. Tightened by default (tracks, rendition and
          master-file settings, input diagnostics), the answer's `projection`
          naming what was removed: `full=true` returns the raw records.
        - `get_asset` — one asset (`asset_id`). `status` is `preparing` until Mux
          has processed the file (about the film's length), then `ready` or
          `errored` (with `errors`).
        - `get_ingest` — one direct upload made by `mux_act` op='create_upload'
          (`upload_id`): `status` is `waiting` until the file is PUT, then
          `asset_created` with its `asset_id` (or `errored`, `cancelled`,
          `timed_out`).

        Every PUBLIC playback id carries its `stream_url`
        (`https://stream.mux.com/<id>.m3u8`, HLS), the address a player takes.

        Args:
            op: What to read (see above).
            asset_id: The asset, for `get_asset`.
            upload_id: The direct upload, for `get_ingest`; or, with `list_assets`, the upload whose asset to find.
            limit: Assets per page, 1 to 100 (`list_assets`).
            cursor: `next_cursor` of the previous page (`list_assets`).
            full: `list_assets`: the raw records instead of the tightened view.
        """
        if op not in _READ_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_READ_OPS)}.")
        if op == "list_assets":
            _refuse_ignored(op, asset_id=asset_id)
            return _view(with_stream_urls(_run(lambda: _client().list_assets(
                limit=limit, cursor=cursor, upload_id=upload_id))), full, _DROP_ASSETS)
        _refuse_ignored(op, limit=limit, cursor=cursor, full=full)
        if op == "get_asset":
            _refuse_ignored(op, upload_id=upload_id)
            aid = _need(asset_id, "asset_id", op)
            return with_stream_urls(_run(lambda: _client().get_asset(aid)))
        _refuse_ignored(op, asset_id=asset_id)
        uid = _need(upload_id, "upload_id", op)
        return _run(lambda: _client().get_upload(uid))

    @mcp.tool(name="mux_act", annotations=ACTE)
    def mux_act(
        op: Literal["create_upload", "create_asset"],
        input_url: Optional[str] = None,
        title: Optional[str] = None,
        passthrough: Optional[str] = None,
        playback_policy: Optional[Literal["public", "signed"]] = None,
        video_quality: Optional[Literal["basic", "plus", "premium"]] = None,
        max_resolution_tier: Optional[Literal["1080p", "1440p", "2160p"]] = None,
        cors_origin: Optional[str] = None,
        timeout: Optional[int] = None,
        test: bool = False,
    ) -> Any:
        """Get a video into Mux. Each call makes a new upload or asset (never
        retried, no deduplication): Mux bills storage and delivery per video.

        `op`:
        - `create_upload` — a direct upload address. The answer's `data.url` takes
          the video file in ONE HTTP PUT, with no credential (the address is the
          authorisation), from wherever the file is: e.g.
          `curl -X PUT --upload-file film.mp4 -H "Content-Type: video/mp4" "<url>"`.
          The file never passes through this tool. Then `mux` op='get_ingest' gives
          the `asset_id`, and op='get_asset' says when it is `ready`. The address
          expires after `timeout` seconds (60 to 604800, default 3600).
        - `create_asset` — a video from a file Mux fetches itself at `input_url`
          (https, public or pre-signed). The answer's `data.id` is the asset.

        Playback is `public` unless `playback_policy="signed"` (a signed playback
        needs tokens this connector does not make). `test=true` makes a free
        10-second watermarked video, deleted after a day: for trying the flow.

        Args:
            op: What to make (see above).
            input_url: The https address of the file, for `create_asset`.
            title: The video's title, shown in the Mux dashboard.
            passthrough: Up to 255 characters of your own, returned on the asset, e.g. to recognise it later.
            playback_policy: `public` (default) or `signed`.
            video_quality: `basic`, `plus` or `premium`, billed differently; Mux's default when omitted.
            max_resolution_tier: Highest resolution stored and streamed: `1080p`, `1440p` or `2160p`.
            cors_origin: `create_upload` only: the origin allowed to PUT from a browser, or `*`.
            timeout: `create_upload` only: seconds before an unused address expires, 60 to 604800.
            test: Make a free 10-second test video instead of a real one.
        """
        if op not in _ACT_OPS:
            raise _bad(f"Unknown op {op!r}: expected one of {', '.join(_ACT_OPS)}. "
                       "Nothing was done.")
        settings = dict(playback_policy=playback_policy, video_quality=video_quality,
                        max_resolution_tier=max_resolution_tier, passthrough=passthrough,
                        title=title, test=test or None)
        if op == "create_upload":
            _refuse_ignored(op, input_url=input_url)
            return _run(lambda: _client().create_upload(
                cors_origin=cors_origin, timeout=timeout, **settings))
        _refuse_ignored(op, cors_origin=cors_origin, timeout=timeout)
        url = _need(input_url, "input_url", op)
        return with_stream_urls(_run(lambda: _client().create_asset(url, **settings)))
