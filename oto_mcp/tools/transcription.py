"""Transcription — a project audio file becomes a project page (ADR 0074).

MCP face. Two tools. `transcription_create`: the agent designates a file by its
REFERENCE (`file_source`, typically `project_file`), the server reads the bytes
and drops off a JOB — it does NOT block the agent (#674, ruling of 22/09/2026:
a long connector must never make it wait online). `transcription_status`
re-reads that job: running, done (with the page), or failed (with the refusal).

Dropping off and re-reading are those of the REST resource (`capabilities/transcription.py`,
guards and order documented there); this module only carries the ambient project (`_project`),
the translation of refusals into MCP errors, and the connector probe. The background work
(Mistral call, page) lives in `oto_mcp/transcription_worker.py`.

3-field credential (ADR 0011): the key (secret), the language and the vocabulary (non
secret) — one instance = one key × one vocabulary, attachable to a project by slot.
"""
from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access, file_source
from ..capabilities import transcription as _transcription
from ..capabilities._types import AuthzDenied
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError


def _refus(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — covers `auth` ONLY: `GET /v1/models` is
    not billed and rejects an invalid key. It says nothing about the account balance."""
    from oto.tools.mistral import MistralClient

    MistralClient(api_key=fields["api_key"]).list_models()


def register(mcp: FastMCP) -> None:
    connector_verify.register("transcription", _verify)

    @mcp.tool()
    def transcription_create(source: dict, vocabulary: str | None = None,
                             vocabulary_replace: bool = False) -> dict:
        """Start transcribing an audio recording (site visit, meeting, voice note)
        into a new page of the project — ASYNCHRONOUS, returns a job reference
        immediately (a ~30 min recording takes 20 s to 5 min to process). Needs
        `_project` (the page's project). Poll `transcription_status(job_id)` for
        the result — the page, one paragraph per speaker turn, never the raw text.

        Args:
            source: the file, never its bytes. Project file:
                `{"kind":"project_file","project_id":<id>,"file_id":<id>}` (ids from
                oto_project_files op=list); also `drive`, `gmail`, `url`.
            vocabulary: optional extra words to spell right for THIS recording
                (names, materials), separated by commas or newlines. Added to the
                connector's vocabulary; 100 words at most overall, the surplus is
                reported by transcription_status (`vocabulary_dropped`).
            vocabulary_replace: true = use only `vocabulary`, ignore the connector's.
        """
        pid = access.current_project()
        if pid is None:
            raise _refus("transcription_create writes a project page: pass "
                         "`_project=<id>` (the project that will receive the page).")
        sub = access.current_user_sub_or_raise()
        try:
            ref = _transcription.deposer(
                sub, pid,
                lambda: file_source.resolve(source, max_bytes=_transcription.MAX_AUDIO_BYTES),
                vocabulary=vocabulary, vocabulary_replace=vocabulary_replace)
        except AuthzDenied as e:
            # The credential resolver's refusal is already an actionable MCP error
            # (connector to activate, quota): return it as-is, not its translation.
            if isinstance(e.__cause__, McpError):
                raise e.__cause__ from None
            raise _refus(str(e)) from None
        return {**ref, "note": "Re-read with transcription_status(job_id)."}

    @mcp.tool()
    def transcription_status(job_id: int) -> dict:
        """Read a transcription job started by `transcription_create`. Returns
        `{status: pending|running|done|failed, ...}` — on `pending`, its place in the
        queue (`queue_position`) and an ESTIMATED wait (`estimated_wait_s`); on
        `pending`/`running`, `retry_after_s`: re-read then, a queued job is never lost,
        do not resubmit it. On `done`, the page `{id, title, url}` plus
        words/duration/speakers; on `failed`, `error`."""
        sub = access.current_user_sub_or_raise()
        try:
            return _transcription.relire(sub, int(job_id), transcript=False)
        except AuthzDenied as e:
            raise _refus(str(e)) from None
