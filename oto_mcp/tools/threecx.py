"""3CX — phone system, READ-ONLY: call log, recordings.

Credential = the address of the phone system (`base_url`) + ONE access: API client
(`client_id`/`client_secret`) or user account (`username`/`password`),
resolved per call via `access.resolve_credential_fields("threecx")` (ADR 0011).
The token carries the rights of that access: what it cannot see, no tool
can see.

**Surface**:
- `threecx_call` (list/export) — one page of the call log over a period,
  or the whole period as a CSV file (a day's "call traces"),
  optionally reduced to the recorded segments;
- `threecx_recording` — the audio of a recording, rendered by
  `file_content.render_for_agent` (signed URL to private storage).

`base_url` is a destination chosen by the user: every client construction goes
through `egress.check_url` (tool AND probe).
"""
from __future__ import annotations

import csv
import io
import re
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress, output_projection
from ..csv_formules import cellule
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

_NAME = "threecx"
# The fields of each access mode (`auth_mode`, the card's discriminator).
_ACCES = {"api_client": ("client_id", "client_secret"), "user": ("username", "password")}
# Body columns of a call-log row: rendered as a size in the trimmed view.
_CORPS = ("QualityReport", "Summary", "Transcription")
_ADRESSE = ("CallHistoryId", "StartTime", "SrcRecId", "DstRecId")
# Export: the file's columns, in order. `SegmentId` and `CallId` are not in it:
# upstream numbers them per response, they identify nothing.
_COLONNES = (
    "StartTime", "Direction", "CallType", "Status", "Answered",
    "SourceDn", "SourceCallerId", "SourceDisplayName",
    "DestinationDn", "DestinationCallerId", "DestinationDisplayName",
    "RingingDuration", "TalkingDuration", "CallCost", "Reason",
    "MainCallHistoryId", "CallHistoryId", "CdrId", "SrcRecId", "DstRecId",
)
_DUREES = ("RingingDuration", "TalkingDuration")
_PAGE_EXPORT = 500
_EXPORT_PAGES_MAX = 200  # 100,000 segments ; a day has a few thousand
_DUREE_ISO = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _champs(fields: dict) -> dict:
    """`base_url` + the pair for the chosen mode (`auth_mode`), NON-EMPTY. An empty
    field passed to the client would raise `MissingCredential` there in the lib's
    name: we refuse it here, in the connector's name."""
    mode = (fields.get("auth_mode") or "").strip()
    if mode not in _ACCES:
        raise ValueError(f"3CX credential: auth_mode must be one of "
                         f"{sorted(_ACCES)} — got {mode!r}")
    noms = ("base_url",) + _ACCES[mode]
    vides = [n for n in noms if not (fields.get(n) or "").strip()]
    if vides:
        raise ValueError(f"3CX credential incomplete ({mode}): {', '.join(vides)} empty")
    return {n: fields[n] for n in noms}


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """An argument provided that THIS op does not use is an error of intent.
    Tested with `is not None`: a provided `0` is an intent too."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op='{op}' does not use {name} — {hint}")


def _secondes(value):
    """An ISO 8601 duration (`PT1M16.4S`) in seconds, rounded to the tenth; an
    unreadable value is returned as is rather than lost."""
    m = _DUREE_ISO.match(value) if isinstance(value, str) else None
    if not m:
        return value
    j, h, mn, s = m.groups()
    return round(int(j or 0) * 86400 + int(h or 0) * 3600 + int(mn or 0) * 60
                 + float(s or 0), 1)


def _csv(rows: list[dict]) -> bytes:
    """The traces file: `;` and UTF-8 BOM, the form a French spreadsheet
    opens without an import wizard; durations in seconds; no text cell
    opens as a formula."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_COLONNES, delimiter=";", extrasaction="ignore",
                       lineterminator="\n")
    w.writeheader()
    for r in rows:
        ligne = {**r, **{d: _secondes(r.get(d)) for d in _DUREES}}
        w.writerow({k: cellule(v) for k, v in ligne.items()})
    return buf.getvalue().encode("utf-8-sig")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"3CX: access denied (HTTP {status}) — invalid credentials, two-factor "
                "authentication enabled, or a permission missing for this account.")
    if status == 404:
        return "3CX: not found (HTTP 404)."
    if status >= 500:
        return f"3CX is temporarily unavailable (HTTP {status}) — retry later."
    return f"3CX rejected the request (HTTP {status}): {str(e.body)[:400]}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe: the first page (one row) of today's call
    log, the smallest authenticated call that exercises the relevant permission.
    An empty page is a possible state, never a refusal."""
    from datetime import date, timedelta

    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.threecx import ThreeCXClient

    try:
        champs = _champs(fields)
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))
    egress.check_url(champs["base_url"], connector="threecx")
    today = date.today()
    try:
        ThreeCXClient(**champs).list_calls(
            today.isoformat(), (today + timedelta(days=1)).isoformat(), top=1)
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"3CX HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"3CX HTTP {e.status_code}: {e.body}")


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.threecx import ThreeCXClient

    from .. import file_content

    connector_verify.register(_NAME, _verify)

    def _client() -> ThreeCXClient:
        try:
            champs = _champs(access.resolve_credential_fields(_NAME))
        except ValueError as e:
            raise _bad(str(e))
        egress.check_url(champs["base_url"], connector="threecx")
        return ThreeCXClient(**champs)

    def _run(fn):
        try:
            return fn()
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except ValueError as e:
            raise _bad(str(e))

    def _periode_entiere(date_from: str, date_to: str, recorded_only: bool) -> list[dict]:
        """The whole log of the period, page after page, up to the incomplete
        page; bounded in advance so an export never runs endlessly."""
        client = _client()
        rows: list[dict] = []
        skip = 0
        for _ in range(_EXPORT_PAGES_MAX):
            page = _run(lambda: client.list_calls(
                date_from, date_to, top=_PAGE_EXPORT, skip=skip,
                recorded_only=recorded_only))
            rows.extend(page["calls"])
            if page["next_skip"] is None:
                return rows
            skip = page["next_skip"]
        raise _bad(f"op='export': more than {_EXPORT_PAGES_MAX * _PAGE_EXPORT} segments "
                   "in the period — narrow date_from / date_to (a day, a week)")

    @mcp.tool()
    def threecx_call(
        date_from: str,
        date_to: str,
        op: Literal["list", "export"] = "list",
        recorded_only: bool = False,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The phone system's call log over a period, every extension and
        direction: one row per call segment (caller, callee, start time, ringing
        and talking durations, status, reason). A recorded segment carries
        `SrcRecId`/`DstRecId`, the id to pass to `threecx_recording`.

        `op="list"` returns one page. Paging counts call-log rows BEFORE
        `recorded_only` filters them: a page may hold fewer rows than `top` and
        still not be the last. Continue with `skip=next_skip` until `next_skip`
        is null. The quality report, summary and transcription of each row are
        DROPPED by default (`<name>_length` says their size); `fields=["*"]`
        returns the raw rows.

        `op="export"` reads the WHOLE period and returns it as one CSV file (`;`
        separated, UTF-8, durations in seconds) — the call records of a day or a
        week, inline when small, otherwise as a short-lived signed URL. Bounded
        to 100,000 segments.

        Args:
            date_from: start, yyyy-MM-dd (midnight UTC) or an ISO 8601 instant
                with its offset, e.g. 2026-09-30T00:00:00+02:00.
            date_to: end, same format.
            op: list (default) | export.
            recorded_only: keep only the segments that have a recording.
            top: op="list" only — call-log rows per page, 1 to 500 (default 100).
            skip: op="list" only — the `next_skip` of the previous page.
            fields: op="list" only — omit for the trimmed view; `["*"]` for raw
                rows; a list of names for exactly those.
        """
        if op == "export":
            _refuse_ignored(op, "the export reads the whole period and returns a file",
                            top=top, skip=skip, fields=fields)
            rows = _periode_entiere(date_from, date_to, recorded_only)
            nom = f"appels-3cx-{date_from[:10]}-{date_to[:10]}.csv"
            sub = access.current_user_sub_or_raise()
            try:
                out = file_content.render_for_agent(
                    _csv(rows), nom, "text/csv", sub=sub, prefix="threecx-files")
            except file_content.MediaUnavailable as e:
                raise _bad(str(e)) from None
            return {**out, "rows": len(rows)}
        page = _run(lambda: _client().list_calls(
            date_from, date_to, top=100 if top is None else top,
            skip=0 if skip is None else skip, recorded_only=recorded_only))
        rows, notice = output_projection.summarize(
            page["calls"], body_fields=_CORPS, fields=fields, always=_ADRESSE)
        return {"calls": rows, "next_skip": page["next_skip"],
                **({"projection": notice} if notice else {})}

    @mcp.tool()
    def threecx_recording(rec_id: int) -> dict:
        """The audio of a call recording (WAV), as a short-lived signed URL.

        Args:
            rec_id: the `SrcRecId` or `DstRecId` of a `threecx_call` row.
        """
        audio = _run(lambda: _client().download_recording(rec_id))
        sub = access.current_user_sub_or_raise()
        try:
            return file_content.render_for_agent(
                audio["content"], audio["filename"], audio["content_type"],
                sub=sub, prefix="threecx-files")
        except file_content.MediaUnavailable as e:
            raise _bad(str(e)) from None
