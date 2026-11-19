"""The usage signal of a credential that did not resolve (ADR 0017), split from
`resolve.py` (its 500-line ratchet). `resolve` imports it by name, so a write on the
facade (`access._emit_connector_failure = …`) still reaches the call site."""
from __future__ import annotations

import logging

from .. import db
from . import scope

logger = logging.getLogger(__name__)


def _emit_connector_failure(provider: str, sub: str) -> None:
    """Best-effort: a `tool_calls(kind='connector', ok=False)` row = "credential
    resolution failed for this provider/sub". Never blocking, never an
    exception that would mask the original McpError (monitoring does not break the service)."""
    try:
        org = scope.current_org(sub)
    # noqa: SILENT — the usage signal never breaks the resolution it observes
    except Exception:
        org = None
    try:
        db.insert_tool_call({
            "kind": "connector", "tool": provider, "sub": sub, "org_id": org,
            "ok": False, "error": "credential_resolution_failed",
        })
    except Exception:  # noqa: BLE001
        logger.debug("connector failure emit failed", exc_info=True)
