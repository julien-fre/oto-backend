"""LightOn — sovereign document indexing (API v3, api.lighton.ai):
hybrid retrieval (search), grounded RAG (ask), parse → Markdown, structured
extraction, per-workspace ingestion.

Wraps `oto.tools.lighton.LightOnClient` (v3 — the v2 API of the Paradigm
app is deprecated and no longer covered). 3-field credential
(API key + optional base URL for private instance + optional default
`workspace_id`) → generic multi-field model (ADR 0011), resolved per call via
`access.resolve_credential_fields("lighton")`. BYO only (the LightOn account
belongs to the customer — the credential IS the grant).

The credential's `workspace_id` makes the instance (ADR 0038) "one key × one
workspace": an instance linked to a project scopes search/ask/upload by default
to its workspace; the tool's explicit argument always wins.

LightOn billing (lighton.ai/pricing): ingestion per page, retrieval per
query (search AND ask), vector storage per GB.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress, file_source
from ..connectors import verify as connector_verify


def _check_base_url(base_url) -> None:
    """Egress guard on the private-instance URL, when set.

    Empty = the SaaS, a constant of the lib. Filled in, it designates a
    self-hosted instance — hence potentially a host on the platform's internal
    network (`oto_mcp/egress.py`)."""
    valeur = (base_url or "").strip()
    if valeur:
        egress.check_url(valeur, connector="lighton")


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET workspaces` (already in the client — `list_workspaces`): LightOn
    bills ingestion (per page), retrieval (`search`/`ask`, per
    query) and vector storage — NOT listing workspaces, a management
    operation, not retrieval. No `/me` or balance otherwise.

    ⚠️ `LightOnClient._request` raises a BARE `RuntimeError` (no typed
    `status_code`) on a refusal — lands on `unknown`, never `unauthorized`. No
    per-resource scoped key model documented at LightOn: honest rather
    than precise, reopenable if the client starts typing its errors.

    **Authenticated ≠ usable** (oto#69 class): does not distinguish scope.
    """
    from oto.tools.lighton import LightOnClient

    _check_base_url(fields.get("base_url"))
    LightOnClient(
        api_key=fields["api_key"], base_url=fields.get("base_url"),
    ).list_workspaces()


def register(mcp: FastMCP) -> None:
    from oto.tools.lighton import LightOnClient

    connector_verify.register("lighton", _verify)

    def _creds() -> dict:
        return access.resolve_credential_fields("lighton")

    def _client(creds: dict) -> LightOnClient:
        _check_base_url(creds.get("base_url"))
        return LightOnClient(api_key=creds.get("api_key"),
                             base_url=creds.get("base_url") or None)

    def _default_workspace(creds: dict) -> Optional[int]:
        raw = (creds.get("workspace_id") or "").strip()
        return int(raw) if raw.isdigit() else None

    def _run(fn):
        """Runs a LightOn call: translates an error into an actionable
        McpError (401 key / 403 account rights / 5xx retry)."""
        creds = _creds()
        client = _client(creds)
        try:
            return fn(client, _default_workspace(creds))
        except McpError:
            raise
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("LightOn 401"):
                msg = "LightOn key invalid or revoked (401). Check the key you set."
            elif msg.startswith("LightOn 403"):
                msg = ("The LightOn account for this key has no access to this "
                       f"resource/operation (403). {msg}")
            elif msg.startswith("LightOn 5"):
                msg = (f"LightOn is temporarily unavailable ({msg}). "
                       "Try again in a moment.")
            raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))
        except Exception as e:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"LightOn could not process the request ({e})."))

    def _resolve_source(source: dict):
        try:
            return file_source.resolve(source)
        except file_source.FileSourceError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))

    def _strip_images(payload: dict) -> dict:
        """Strips the base64 page images from retrieval results — the v3 API
        attaches them BY DEFAULT on /ask (~250 KB PER chunk, seen 23/07: 3 chunks
        = 820 KB of response), untenable in an LLM context."""
        for r in payload.get("results", []) or []:
            if isinstance(r, dict):
                r.pop("image", None)
        return payload

    @mcp.tool()
    def lighton_search(
        query: str,
        workspace_ids: Optional[list[int]] = None,
        file_ids: Optional[list[int]] = None,
        max_results: int = 5,
        mode: str = "text",
    ) -> dict:
        """Semantic retrieval over the LightOn document index (sovereign,
        EU-hosted): hybrid dense + keyword search with multivector reranking.
        Returns ranked chunks with provenance (file, pages, scores) — NO LLM
        generation (compose the answer yourself, or use `lighton_ask`).

        Billed: 1 retrieval credit per call.

        Args:
            query: natural-language query (max 1500 chars).
            workspace_ids: restrict to these workspaces. Default: the
                connector's configured workspace if set, else the whole
                corpus the key can access. Cannot combine with file_ids.
            file_ids: restrict to specific documents.
            max_results: chunks returned after reranking (1-50).
            mode: "text" (default) or "vision" (searches VLM-embedded page
                images — scanned docs, diagrams).
        """
        return _strip_images(_run(lambda c, ws: c.search(
            query,
            workspace_ids=workspace_ids or (None if file_ids else ([ws] if ws else None)),
            file_ids=file_ids, max_results=max_results,
            mode=mode if mode != "text" else None)))

    @mcp.tool()
    def lighton_ask(
        query: str,
        workspace_ids: Optional[list[int]] = None,
        file_ids: Optional[list[int]] = None,
        max_results: int = 5,
        model: Optional[str] = None,
    ) -> dict:
        """Full RAG over the LightOn document index: retrieves the most
        relevant chunks then generates an LLM answer grounded in them, with
        source citations. Returns `{answer, results[]}`.

        Billed: 1 retrieval credit per call. For chunks without generation
        (cheaper composition by the agent), use `lighton_search`.

        Args:
            query: natural-language question (max 1500 chars).
            workspace_ids / file_ids: scoping — same rules as
                `lighton_search` (connector's configured workspace by
                default).
            max_results: context chunks (1-50).
            model: generation LLM (e.g. "mistral-large-latest"); platform
                default if omitted.
        """
        return _strip_images(_run(lambda c, ws: c.ask(
            query,
            workspace_ids=workspace_ids or (None if file_ids else ([ws] if ws else None)),
            file_ids=file_ids, max_results=max_results, model=model)))

    @mcp.tool()
    def lighton_parse(source: dict) -> dict:
        """Parse a document into clean structured Markdown (LightOn OCR
        pipeline — PDF, Office, images, HTML). One-shot processing: the
        document is NOT added to the search index (use
        `lighton_upload_document` for that).

        `source` (object, `kind` selects the origin):
        - Drive: `{"kind":"drive","file_id":"<id>"}`
        - Gmail attachment: `{"kind":"gmail","message_id":"<id>","filename":"<name>"}`
        - URL: `{"kind":"url","url":"https://…"}`
        - Project file: `{"kind":"project_file","project_id":<id>,"file_id":<id>}`
          (ids from oto_project_files op=list)

        Sync limits: ~20 MB / 15 pages. Returns `{status, result, usage}` —
        the Markdown is in `result`.
        """
        rf = _resolve_source(source)
        return _run(lambda c, ws: c.parse_bytes(rf.data, rf.filename))

    @mcp.tool()
    def lighton_extract(source: dict, schema: dict) -> dict:
        """Extract structured fields from a document into a typed JSON Schema
        (LightOn). One-shot processing, document NOT indexed.

        Sync limits: ~20 MB / 15 pages. Returns `{status, result, usage}`.

        Args:
            source: same shape as `lighton_parse` (drive/gmail/url/project_file).
            schema: JSON Schema object describing the fields to extract,
                e.g. `{"type":"object","properties":{"invoice_number":
                {"type":"string"}}}`.
        """
        rf = _resolve_source(source)
        return _run(lambda c, ws: c.extract_bytes(rf.data, rf.filename, schema))

    @mcp.tool()
    def lighton_files(
        workspace_ids: Optional[list[int]] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        filename: Optional[str] = None,
        page: Optional[int] = None,
    ) -> dict:
        """List documents in the LightOn index (paginated). `search` orders
        results by semantic relevance (quick "find my doc").

        Returns `{count, next, previous, results[]}` (id, filename, title,
        workspace, status, total_pages…).

        Args:
            workspace_ids: filter by workspaces (default: the connector's
                configured workspace if set, else all accessible).
            search: semantic relevance query.
            status: ingestion status filter (e.g. "pending,embedded").
            filename: case-insensitive partial filename match.
        """
        return _run(lambda c, ws: c.list_files(
            workspace_ids=workspace_ids or ([ws] if ws else None),
            search=search, status=status, filename=filename, page=page))

    @mcp.tool()
    def lighton_upload_document(
        source: dict,
        workspace_id: Optional[int] = None,
        title: Optional[str] = None,
    ) -> dict:
        """Upload + index a document into a LightOn workspace — it becomes
        searchable via `lighton_search`/`lighton_ask` once its status reaches
        `embedded` (check with `lighton_files`).

        Billed per ingested page.

        Args:
            source: same shape as `lighton_parse` (drive/gmail/url/project_file).
            workspace_id: destination workspace. REQUIRED unless the
                connector instance has a configured default workspace.
                List available ones with `lighton_workspaces`.
            title: display title (default: filename).
        """
        # gate on workspace BEFORE resolving the source (no useless download)
        wid = workspace_id or _default_workspace(_creds())
        if not wid:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message="workspace_id required (no default workspace "
                        "configured on the connector) — list them via "
                        "lighton_workspaces."))
        rf = _resolve_source(source)
        return _run(lambda c, ws: c.upload_file_bytes(
            rf.data, rf.filename, wid, title=title))

    @mcp.tool()
    def lighton_delete_document(file_id: int) -> dict:
        """Permanently delete a document and its index from LightOn.
        Irreversible.

        Args:
            file_id: document id (from `lighton_files`).
        """
        _run(lambda c, ws: c.delete_file(file_id))
        return {"deleted": file_id}

    @mcp.tool()
    def lighton_workspaces(name: Optional[str] = None) -> dict:
        """List LightOn workspaces accessible to the configured key —
        isolated document collections (manually fed, or synced from
        SharePoint / Google Drive). Use a returned `id` as `workspace_id`
        in upload/search/ask.

        Args:
            name: filter by name.
        """
        return _run(lambda c, ws: c.list_workspaces(name=name))
