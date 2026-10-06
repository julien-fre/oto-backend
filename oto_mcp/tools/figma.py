"""Figma — files, image export, comments, FigJam extraction.

Wraps `oto.tools.figma.FigmaClient`. Token resolved per call via
`access.resolve_api_key("figma")` — byo. **Disk cache disabled**
(`cache_enabled=False`): on a multi-user host the file cache is
not keyed by token → cross-user leak.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /v1/me`. What the Figma docs establish:

    - **authenticated** — token in the `X-Figma-Token` header, like the rest of
      the API;
    - **no side effects** — an identity read (`id`, `handle`, `email`);
    - **the cost** — no mention of any particular cost or rate limit
      for this call. Absence of mention is a hint, not proof.

    ⚠️ **Fourth rule of oto#69: a probe never turns its own
    limit into a verdict on the key.** `/v1/me` requires the `current_user:read` scope
    — SEPARATE from the connector's real scopes (file/design reads). A
    token legitimately scoped for real use may refuse THIS call without being
    broken. Figma documents `403` for a missing scope and `401` for a dead/invalid
    token — two distinct codes, hence distinguishable: the 403 raises a
    BARE `RuntimeError` (never `NonAutorise`) so as to land on the verdict
    `unknown` ("I don't know"), NOT `unauthorized` ("replace your key") —
    a false negative here would push people to revoke a working key. The real 401
    raises `NonAutorise`, a deserved `unauthorized` verdict.
    """
    import requests
    from oto.tools.figma.client import FigmaClient

    try:
        infos = FigmaClient(token=fields["key"], cache_enabled=False)._request(
            "GET", "me", use_cache=False) or {}
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 403:
            raise RuntimeError(
                "Figma refuses THIS verification call (403, scope "
                "current_user:read) — that says NOTHING about the key for the "
                "connector's real use (files/design, a different scope). "
                "Inconclusive, not invalid.") from e
        if status == 401:
            raise connector_verify.NonAutorise(
                f"Figma refuses this key (401): {str(e)[:200]}") from e
        raise
    if not infos.get("id"):
        raise RuntimeError(
            "Figma answered without identifying a user for this key — "
            f"unexpected response: {str(infos)[:200]}")


def register(mcp: FastMCP) -> None:
    from oto.tools.figma.client import FigmaClient

    connector_verify.register("figma", _verify)

    def _client() -> FigmaClient:
        key, _ = access.resolve_api_key("figma")
        return FigmaClient(token=key, cache_enabled=False)

    @mcp.tool()
    def figma_get_file(
        file_key: str,
        depth: Optional[int] = None,
        node_ids: Optional[list[str]] = None,
    ) -> dict:
        """Get a Figma/FigJam file structure.

        Args:
            file_key: the key from the file URL (figma.com/file/<KEY>/…).
            depth: limit tree depth (cheaper for big files).
            node_ids: restrict to specific nodes.
        """
        return _client().get_file(file_key, depth=depth, node_ids=node_ids)

    @mcp.tool()
    def figma_file_meta(file_key: str) -> dict:
        """Get a file's metadata only (name, last modified, thumbnail…)."""
        return _client().get_file_meta(file_key)

    @mcp.tool()
    def figma_get_images(
        file_key: str,
        node_ids: list[str],
        format: str = "png",
        scale: float = 2,
    ) -> dict:
        """Export rendered images for nodes. Returns temporary image URLs.

        Args:
            format: png | jpg | svg | pdf.
            scale: scale factor (1–4).
        """
        return _client().get_images(file_key, node_ids, format=format, scale=scale)

    @mcp.tool()
    def figma_get_comments(file_key: str, as_markdown: bool = False) -> dict:
        """List comments on a file."""
        return _client().get_comments(file_key, as_markdown=as_markdown)

    @mcp.tool()
    def figma_post_comment(
        file_key: str,
        message: str,
        comment_id: Optional[str] = None,
    ) -> dict:
        """Post a comment on a file (or reply to `comment_id`)."""
        return _client().post_comment(file_key, message, comment_id=comment_id)
