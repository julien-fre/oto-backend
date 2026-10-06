"""BLS — wages and employment by occupation in the United States (OEWS survey, open data).

Wraps `oto.tools.bls.client.BLSClient` (BLS public API v2). Open-data connector:
no credential, no cascade. Exposed only if activated in DB (activation
notch, ADR 0010).

A single tool, `bls_oews_wages`: the annual wage distribution of an occupation
(6-digit SOC) for one or more areas, in one request whenever possible.

⚠️ **The quota is the PLATFORM's, not the caller's**: without a registration
key, the API serves 25 requests per day to the calling address — so to
all orgs together. The operator lifts this cap (500/day, 50 series per
request) by setting `BLS_API_KEY` in the server environment: `_client()` reads it
and passes it to the client (the lib reads no secret). That is also why `bls` is NOT in
`TESTABLE_NAMESPACES`: a « test » button would spend everyone's quota.

The client call is written out in plain form (`_client().oews_wages(…)`): that is what makes it
verifiable by the version-skew probe.
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError

# 7 series per area, 25 per request without a key: 3 areas per request. 12 areas = 4
# requests at worst, i.e. a sixth of the shared daily quota — beyond that, the caller
# splits and sees it.
_MAX_AREAS = 12

_SOURCE = "U.S. Bureau of Labor Statistics — Occupational Employment and Wage Statistics (OEWS)"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status == 429:
        return "BLS: too many requests (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"BLS is temporarily unavailable (HTTP {status}) — retry later."
    return f"BLS refused the request (HTTP {status}): {e.body}"


def _refusal_message(e) -> str:
    detail = " ; ".join(e.messages) or e.status
    if "threshold" in detail.lower():
        return ("BLS: the public API's daily quota is exhausted (25 requests "
                "per day without a registration key, shared by the whole platform) "
                f"— retry tomorrow. Detail: {detail}")
    return f"BLS refused the request ({e.status}): {detail}"


def register(mcp: FastMCP) -> None:
    from oto.tools.bls.client import BLSClient, BLSRequestError
    from oto.tools.common.errors import UpstreamHTTPError

    def _client() -> BLSClient:
        # OPTIONAL registration key, set by the operator: it lifts the quota
        # (500 requests/day, 50 series per request). Absent, the API serves the keyless
        # regime — the nominal mode of an open data connector, not a fallback. The
        # server is what reads it: the lib no longer reads any secret (oto-core v1.148.0).
        return BLSClient(registration_key=os.environ.get("BLS_API_KEY") or None)

    @mcp.tool()
    def bls_oews_wages(soc: str, areas: Optional[List[str]] = None) -> dict:
        """US wages for one occupation — annual P10/P25/median/P75/P90, mean and
        employment, per area. Source: BLS OEWS (open data, no key needed).

        LATEST published year only (the API serves no OEWS history); the year is in
        the result. Wages are published at the 6-digit SOC: an 8-digit O*NET-SOC
        code ("15-1299.08") is accepted but its suffix is STRIPPED — the figures
        then cover the whole SOC ("15-1299"), and the result says so in `note`.

        Shared daily quota: one upstream request per 3 areas, 25 requests/day for
        the whole platform — batch areas in ONE call rather than one call per area.

        Returns — `{"source", "soc", "year", "note"?, "requests", "areas": [...],
        "messages"}`. Each area: `area`, `area_type` (N|S|M), `area_code`, `year`,
        `percentiles` {p10,p25,p50,p75,p90} and `mean` (annual USD), `employment`,
        `footnotes`, `raw`, `missing`, `series_ids`. A value BLS withholds or caps
        comes back `null` with its raw form ("-") in `raw` and the reason in
        `footnotes` — never a guessed number. `missing` lists measures with no
        series for that SOC × area (small metros, rare occupations).

        Args:
            soc: SOC code — "15-1299" or "151299" (or an O*NET-SOC, suffix stripped).
            areas: default ["US"]. Up to 12 of: "US", a state name or postal abbreviation
                ("Illinois", "IL"), or a 5-digit CBSA code for a metro area
                ("16980" = Chicago-Naperville-Elgin). Metro NAMES are not resolved.
        """
        if areas is None:
            areas = ["US"]
        if not areas:
            raise _bad("`areas` is empty — pass at least one area ('US', a State, "
                       "a 5-digit CBSA code).")
        if len(areas) > _MAX_AREAS:
            raise _bad(f"{len(areas)} areas requested — at most {_MAX_AREAS} per call "
                       "(shared BLS daily quota): split the list.")
        try:
            out = _client().oews_wages(soc, list(areas))
        except ValueError as e:
            raise _bad(str(e))
        except BLSRequestError as e:
            raise _bad(_refusal_message(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

        suffix = out.pop("onet_suffix_stripped", None)
        result = {"source": _SOURCE, **out}
        if suffix is not None:
            result["note"] = (
                f"O*NET-SOC suffix '.{suffix}' stripped: BLS publishes wages at the "
                f"6-digit SOC, so these figures cover all of {out['soc']}"
                + ("." if suffix == "00" else
                   f", not only the detailed occupation {out['soc']}.{suffix}."))
        return result
