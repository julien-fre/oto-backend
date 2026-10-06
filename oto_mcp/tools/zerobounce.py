"""ZeroBounce — email verification (deliverability).

Wraps `oto.tools.zerobounce.ZeroBounceClient`. Key resolved per call via
`access.resolve_api_key("zerobounce")` — byo. No platform key.
"""
from __future__ import annotations

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access


def register(mcp: FastMCP) -> None:
    from oto.tools.zerobounce.client import ZeroBounceClient

    def _client() -> ZeroBounceClient:
        key, _ = access.resolve_api_key("zerobounce")
        return ZeroBounceClient(api_key=key)

    @mcp.tool()
    def zerobounce_verify_email(email: str) -> dict:
        """Verify a single email address.

        Returns a status: valid (deliverable), invalid, catch-all, unknown,
        spamtrap, abuse, do_not_mail (disposable/role-based).
        """
        return _client().verify_email(email)

    @mcp.tool()
    def zerobounce_verify_batch(emails: list[str]) -> dict:
        """Verify up to 200 emails in one call. Returns the per-email results."""
        if len(emails) > 200:
            raise McpError(ErrorData(
                code=INVALID_PARAMS, message="Maximum 200 emails par batch."))
        return {"results": _client().verify_batch(emails)}

    @mcp.tool()
    def zerobounce_credits() -> dict:
        """Remaining ZeroBounce verification credits on the account."""
        return {"credits": _client().get_credits()}
