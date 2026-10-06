"""Brightdata — empty shell (scaffold).

Connector wired on the platform side (`providers/brightdata.py` entry + platform key +
quota), but **no functional tool** is exposed yet: the Bright
Data products (SERP API, Web Unlocker, Web Scraper/Datasets) remain to be implemented.

`register(mcp)` is called by `register_all` (derived from the registry) but
registers nothing for now — the `_client`/`_run` helpers are in place for the
future `brightdata_*` tools (resolution + platform usage counting identical to
serper/serpapi). See `oto.tools.brightdata.client.BrightDataClient` (oto-core) for
the products documented as TODO.
"""
from __future__ import annotations

from fastmcp import FastMCP

from .. import access


def register(mcp: FastMCP) -> None:
    # Imports/helpers in place for the future tools — no tool exposed (empty shell).
    from oto.tools.brightdata.client import BrightDataClient  # noqa: F401

    def _client() -> tuple[BrightDataClient, bool]:
        key, is_platform = access.resolve_api_key("brightdata")
        return BrightDataClient(api_key=key), is_platform

    def _run(method: str, **kwargs) -> dict:
        """Resolves the key, calls the client method, counts platform usage."""
        client, is_platform = _client()
        result = getattr(client, method)(**kwargs)
        if is_platform:
            access.record_platform_usage("brightdata")
        return result

    # TODO — register the product tools here (see the module docstring):
    #   brightdata_serp(query, engine="google", ...)  -> parsed SERP
    #   brightdata_unlock(url, data_format=None, ...)  -> HTML / Markdown
    #   brightdata_dataset_*(...)                      -> async datasets
    _ = (_client, _run)  # referenced to avoid a premature "unused" warning.
