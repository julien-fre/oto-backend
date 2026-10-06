"""The "read status / disconnect" verbs of an OAuth connector — declared
by its module, derived everywhere. Symmetric counterpart of `connector_flow` (`flow.py`,
the "connect" verb) for the status/disconnect pair (oto-dashboard#125).

**The problem this closes.** `me.connector_connect` has a registry (`declare`) fed
at MODULE level by each flow connector: a single fixed path, derived everywhere.
The two other halves of the same gesture — "am I connected?", "disconnect me" —
had no equivalent: the dashboard still built its URL from the connector NAME
(`/api/${name}/oauth/status`, `DELETE /api/${name}/oauth`), for the OAuth
connectors of the time (atlassian, folkmcp, google). This module closes the
`disconnect` half; `status` stays declarable HERE (same shape as `declare`), but
nothing calls it in this batch.

⚠️ **Only one declarer remains since 2026-09-09**: `google`. Atlassian and folkmcp
left with the MCP federation (ADR 0069). The registry keeps its shape — a seam is
not folded away because it has only one occupant left.

**Why `status` exists without being wired.** Constraint 1 of oto-dashboard#125
(ruling of 04/09/2026) forbids `me.connector_status` from querying an `auth.*`
module in parallel with `/api/me`: its state MUST come from `access.status_for`,
the SAME source, never from a second call that could diverge. The `status` verb
of this registry is therefore symmetric infrastructure (same shape as
`disconnect`, so that a future connector wanting a dedicated reader does not have
to invent a second pattern) — `capabilities/connectors/oauth_status.py` wires
ONLY `disconnect` to it, never `status`.

**What the seam guarantees.** A pure MODULE-level declaration (like
`connector_flow.declare`), readable at import, with no side effect. The callables
themselves can lazily import their `auth.*` module (it builds HTTP clients and
reads its config at load) — the declaration does not force them to load before
the actual call, exactly like `federated_oauth._federation()._module()`.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StatusFlow:
    connector: str
    # (ctx) -> dict, never called by this batch (see the module docstring) — `None` =
    # not declared, which is the case of the only connector wired today.
    status: Optional[Callable[..., dict]] = None
    # (ctx) -> dict — the only verb actually wired by this batch.
    disconnect: Optional[Callable[..., dict]] = None


_FLOWS: dict[str, StatusFlow] = {}


def declare_status(connector: str, *, status: Optional[Callable[..., dict]] = None,
                    disconnect: Optional[Callable[..., dict]] = None) -> None:
    """Declare the status/disconnect verbs of this OAuth connector. Called at
    MODULE level (like `connector_flow.declare`): a pure declaration, readable at
    import, without waiting for the FastMCP mount."""
    _FLOWS[connector] = StatusFlow(connector=connector, status=status, disconnect=disconnect)


def supports(connector: str) -> bool:
    return connector in _FLOWS


def entries() -> dict[str, StatusFlow]:
    return dict(_FLOWS)


async def _run(fabrique: Callable[..., dict], ctx) -> dict:
    """Run the declared callable and return its raw shape — same discipline as
    `connector_flow.start`: a flow can be asynchronous (hosted provider) or
    synchronous-but-blocking (HTTP revocation, see `google_oauth.revoke`), and in
    both cases the single-loop server must never run it in a blocking way."""
    if inspect.iscoroutinefunction(fabrique):
        out = await fabrique(ctx)
    else:
        out = await asyncio.to_thread(fabrique, ctx)
        if inspect.isawaitable(out):
            out = await out
    if not isinstance(out, dict):
        raise TypeError(
            f"the declared verb must return a dict (got {type(out).__name__})")
    return out


async def read_status(connector: str, ctx) -> dict:
    f = _FLOWS[connector].status
    if f is None:
        raise KeyError(f"\"{connector}\" has no status reader declared here")
    return await _run(f, ctx)


async def disconnect(connector: str, ctx) -> dict:
    f = _FLOWS[connector].disconnect
    if f is None:
        raise KeyError(f"\"{connector}\" has no disconnect verb declared here")
    return await _run(f, ctx)
