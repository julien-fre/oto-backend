"""Shared base of the `pennylane` connector modules — oto-backend#872.

The connector spans several modules (`tools/pennylane*.py`, see
`Connector.modules` in the registry). This file holds what they have in common:
key resolution, the two error shapes, and above all the **translation
of an upstream refusal into an exception**.

Why one file rather than a copy per module: the oto-core client returns
a refusal as a *value* (`{"error": "422", "details": …}`) and not as an
exception. The piece that catches this must exist only once — duplicated,
it diverges, and the forgotten module is the one that will write into an accounting system without
anyone noticing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError
from .. import access

if TYPE_CHECKING:  # annotation of `_client()` only — never evaluated
    from oto.tools.pennylane import PennylaneClient


def _client() -> PennylaneClient:
    """The Pennylane client for THIS caller's key.

    The real import is done in the body, not at module load: tests replace
    `PennylaneClient` on the package, and a deferred import is
    what lets them do so.

    The return type is annotated with the BARE class, and not only for
    readability: the version-skew probe reads this annotation to know against
    which client to check that the methods called here exist in the pinned
    oto-core. Without it, this module would slip through that check.
    """
    from oto.tools.pennylane import PennylaneClient

    key, _is_platform = access.resolve_api_key("pennylane")
    # Redaction applied at the tools boundary by `FieldRedactionMiddleware`
    # (policy of the active org), no longer at client level.
    return PennylaneClient(api_key=key)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Mandatory argument for THIS op — actionable error, never a fallback."""
    if value is None:
        raise _bad(f"op='{op}' requires {name}")
    return value


def _ecrit(appel, geste: str):
    """EXECUTE a Pennylane write and return its result, or RAISE with guidance.

    Takes a function, not a result: since oto-core#77 the client raises on
    upstream refusal, and an exception raised in the argument would never reach
    a check placed after the call. The action must happen here, under the guard.

    The backend's taxonomy already classifies `UpstreamHTTPError`; what this guard
    adds is specific to the connector: telling the agent WHAT TO DO. A 401/403
    on Pennylane is almost never an argument to correct, it is a permission
    missing from the key — and nothing showed it before the failure.
    """
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return appel()
    except UpstreamHTTPError as e:
        st, detail = e.status_code, str(e.body)[:400]
    except RuntimeError as e:
        # Refusal without an HTTP status: network, rate limiting, unreadable body.
        raise _bad(f"Pennylane did not respond to {geste}: {e}") from e

    if st in (401, 403):
        raise _bad(
            f"Pennylane refused {geste} ({st}): this is a PERMISSION missing from the "
            "key, not an argument to correct — replaying it identically will fail "
            "the same way. Each user sets their own key, with their own "
            "scope: a tool being mounted therefore proves NO permission. Read "
            "the key's actual permissions with `pennylane_ref(kind=\"company\")`, "
            f"`scopes` field, then tell the user which one is missing. Detail: {detail}")
    if st == 422:
        raise _bad(f"Pennylane refused the CONTENT of {geste} ({st}): the values "
                   f"sent do not pass its checks. Detail: {detail}")
    if st == 404:
        raise _bad(f"Pennylane cannot find the target of {geste} ({st}): the id "
                   f"does not exist in THIS company. Detail: {detail}")
    raise _bad(f"Pennylane refused {geste} ({st}). Detail: {detail}")
