"""Capability "start a connector's connection flow" — ONE path, all flows.

ADR 0042 §Surface convergence. Before: each flow connector exposed its own
path (`/api/zoho/oauth/start`, `/api/salesforce/oauth/start`), so the front end had one
client function per connector and a hard-coded list of names to decide which to call.
The connector name now travels as a **path parameter** (precedent:
`/api/me/connectors/{name}/session/start`), and what must be supplied is described by the
catalog (`connect.params`) — the dashboard renders a generic form.

The gesture itself stays with the connector (`connector_flow.declare`, called from its
module): this capability only routes, guards, and translates a refusal.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ...connectors import flow as connector_flow
from .._authz import ORG_MEMBER
from .._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding)
from ..registry import CAPABILITIES


class ConnectorConnectInput(BaseModel):
    name: str                                   # the connector, from the path
    params: Optional[dict] = None               # the values of `connect.params`


class ConnectorConnectStarted(BaseModel):
    """The flow is STARTED, nothing is connected. A 200 here says only one thing:
    "here is the consent URL to open". The credential will only exist once the
    provider returns to the callback — a client that treats this response as a
    connection success will show "connected" to someone who has not yet authorized
    anything. For the real state, poll the connector's card (`oto_instance
    op=verify`) after the return.

    `auth_url` is to be opened in a BROWSER: it is a human consent page,
    not an API call. It is single-use and carries a short-lived `state` — storing
    it for later gives a dead link.

    **The shape does NOT depend on the connector** — that is the whole point of this
    single path. `auth_url` is common to all flows; whatever a connector wants to echo
    in addition lives under `details`, its own, and is never needed to act (Zoho puts
    `connector` there, Salesforce the `scope` it kept). A client that reads `details`
    accepts knowing which connector it is wiring up: the seam does not ask it to.
    (A connector refusal never arrives here: a connector with no declared flow
    answers 400 `no_connection_flow`.)"""

    auth_url: str
    details: dict = Field(
        default_factory=dict,
        description="Connector-specific echo — never required to open "
                    "`auth_url`. Its content belongs to the connector's module and "
                    "may change without this contract moving.")


async def _connect(ctx: ResolvedCtx, inp: ConnectorConnectInput) -> dict:
    if not connector_flow.supports(inp.name):
        raise AuthzDenied(
            400, "no_connection_flow",
            f"\"{inp.name}\" has no connection flow: its credential is set "
            "in the card's form.")
    return (await connector_flow.start(inp.name, ctx, inp.params or {})).as_dict()


CAPABILITIES += [
    Capability(
        key="me.connector_connect",
        handler=_connect,
        Input=ConnectorConnectInput,
        authz=ORG_MEMBER,
        Output=ConnectorConnectStarted,
        mcp=None,     # the per-connector MCP faces already exist (oto_zoho_connect…)
        errors=(DeclaredError(400, "no_connection_flow",
                              "this connector has no connection flow: its "
                              "key is SET, not requested"),),
        rest=RestBinding(verb="POST", path="/api/me/connectors/{name}/connect"),
        description=("Starts the connection flow declared by this connector and returns "
                     "the consent URL to open. The expected values are "
                     "described by `connect.params` in the catalog."),
    ),
]
