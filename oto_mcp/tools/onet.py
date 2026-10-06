"""O*NET — the United States occupation reference (O*NET Web Services v2).

Wraps `oto.tools.onet.client.ONetClient`. keyed `api_key` (`X-API-Key` header),
byo-only: the key is free but personal (developer sign-up on
services.onetcenter.org), no platform key.

A single tool, `onet_occupation` — one business object, two gestures (ADR 0047):
- `op="search"`: find an occupation and its O*NET-SOC code by keyword or by code;
- `op="get"`: an occupation's record — title, description, job titles, tasks.

**No parameter is silently ignored**: an op that does not use a provided argument
REFUSES (`_refuse_ignored` pattern).

⚠️ **Written from the v2.0 reference manual, never exercised live**: no key
was available at the time of writing. Paths, auth header and response shapes are
those of the manual; the first real call remains to be made.

Client calls are written out in plain (`_client().get_occupation(…)`): that is what
makes them checkable by the version-skew probe.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

# The API's navigation links (one URL per report and per result): they only serve
# an authenticated HTTP client, not an agent. Returned on `full=True`.
_SEARCH_ITEM_DROP = ("href",)
_GET_DROP = ("summary_contents", "details_contents", "custom_contents", "updated")
_MAX_LIMIT = 100


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"O*NET rejected the API key (HTTP {status}) — check the key set on "
                "this connector (services.onetcenter.org → My Account).")
    if status == 404:
        return f"O*NET: resource not found (HTTP 404) — {e.body}"
    if status == 422:
        return ("O*NET: request refused (HTTP 422) — invalid parameter, nonexistent or "
                f"obsolete O*NET-SOC code, or data missing for this occupation: {e.body}")
    if status == 429:
        return "O*NET: service saturated (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"O*NET is temporarily unavailable (HTTP {status}) — retry later."
    return f"O*NET refused the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection": a 1-result search, the lightest call."""
    from oto.tools.onet.client import ONetClient
    ONetClient(api_key=fields["key"]).search_occupations("engineer", end=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.onet.client import ONetClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("onet", _verify)

    def _client() -> ONetClient:
        key, _ = access.resolve_api_key("onet")
        return ONetClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def onet_occupation(
        op: Literal["search", "get"] = "search",
        keyword: Optional[str] = None,
        code: Optional[str] = None,
        limit: int = 20,
        full: bool = False,
    ) -> dict:
        """A US occupation in the O*NET taxonomy — find it, or read its profile.

        op="search": occupations matching a word, phrase, job title or (partial)
        code, closest first. Returns — `{"start", "end", "total", "occupation":
        [{"code", "title", "tags"}]}`.
        op="get": one occupation by O*NET-SOC code. Returns — `{"code", "title",
        "description", "sample_of_reported_titles", "also_see", "tags",
        "bright_outlook", "tasks": [...], "tasks_total"}`; not every occupation
        carries every field, and one without task data returns `tasks: []`.

        An O*NET-SOC code is 8 digits ("15-1299.08"); its first 6 ("15-1299") are
        the SOC code that US wage statistics are published under.

        Args:
            op: "search" (default) | "get".
            keyword: REQUIRED by search — word, phrase, job title or code.
            code: REQUIRED by get — "15-1299.08"; a 6-digit SOC is read as ".00".
            limit: max results (search) or max tasks (get), 1-100, default 20.
            full: True also returns the API's navigation links (per-result `href`,
                report link lists, data-update history), dropped by default.
        """
        if not 1 <= limit <= _MAX_LIMIT:
            raise _bad(f"`limit` must be between 1 and {_MAX_LIMIT}.")
        if op == "search":
            _refuse_ignored(op, "pass `keyword` (a code can be searched there too).", code=code)
            if not (keyword or "").strip():
                raise _bad("op='search' requires `keyword`.")
            found = _run(lambda: _client().search_occupations(keyword.strip(), end=limit))
            if full:
                return found
            return output_projection.project(found, items_path="occupation",
                                             item_drop=_SEARCH_ITEM_DROP)
        if op == "get":
            _refuse_ignored(op, "pass `code` (the occupation's O*NET-SOC code).",
                            keyword=keyword)
            if not (code or "").strip():
                raise _bad("op='get' requires `code` (e.g. '15-1299.08').")
            occupation = _run(lambda: _client().get_occupation(code))
            try:
                tasks = _client().get_occupation_tasks(code, end=limit)
            except UpstreamHTTPError as e:
                if e.status_code != 422:       # 422 = this occupation has no tasks
                    raise _bad(_upstream_message(e))
                tasks = {"task": [], "total": 0}
            occupation["tasks"] = [t.get("title") for t in tasks.get("task") or []]
            occupation["tasks_total"] = tasks.get("total", len(occupation["tasks"]))
            if full:
                return occupation
            return output_projection.project(occupation, drop=_GET_DROP, items_path="also_see",
                                             item_drop=_SEARCH_ITEM_DROP)
        raise _bad("op must be 'search' or 'get'.")
