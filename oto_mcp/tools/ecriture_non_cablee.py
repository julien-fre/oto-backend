"""The named refusal of a write that a connector does not wire: `<connector>_write_not_wired`.

A READ-ONLY connector keeps its write tools visible, but none of them calls
upstream: each returns this refusal, which tells the agent what the call WOULD have done. PayFit
(24/09/2026) then Inqom (30/09/2026) — same shape, a single home.

An ERROR, not a result: a `{"sent": false}` is too easily read as a success, and
an agent that believes it put someone on payroll or posted an accounting entry is worse
than a refused agent. Same shape as the other named refusals (`connector_disabled`): the
code in the message AND in `data`, `retryable: False`.

The refusal depends on nothing: neither the key nor the client is touched. `what` describes
the action to the agent — the caller only puts identifiers, dates, labels and
totals in it, never a value masked by default (NIR, IBAN…), which would end up in the
call log.
"""
from __future__ import annotations

from typing import Any

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError


def refus(connecteur: str, libelle: str, op: str, action: str, **what: Any) -> McpError:
    """The `<connecteur>_write_not_wired` refusal for `op`: "this would have <action>",
    followed by the elements supplied in `what` (empty ones are omitted)."""
    code = f"{connecteur}_write_not_wired"
    decrit = {k: v for k, v in what.items() if v is not None and v != "" and v != []}
    detail = ", ".join(f"{k}={v!r}" for k, v in decrit.items())
    return McpError(ErrorData(
        code=INVALID_PARAMS,
        message=(f"Refusal `{code}`: this would have {action}"
                 f"{f' ({detail})' if detail else ''} — but the connector does not wire "
                 f"the {libelle} API for writing: nothing was sent to {libelle}. "
                 f"This connector only reads; writing is done in {libelle} "
                 "itself."),
        data={"code": code, "retryable": False, "op": op, "would_have": decrit},
    ))
