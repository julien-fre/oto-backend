"""Generic capabilities "read the state / disconnect" of an OAuth consent —
a fixed path that does not name the connector, symmetric to `me.connector_connect`
(`connect.py`). Closes items 2/3 of oto-dashboard#125: the dashboard widget
built `/api/${name}/oauth/status` and `DELETE /api/${name}/oauth`, the last
two places where a connector name traveled in a URL.

**Scope: google, wired HERE.** ⚠️ There were THREE until 2026-09-09 — atlassian
and folkmcp left with the MCP federation (ADR 0069). A connector outside this
list answers `400 no_oauth_status`: this is not a gratuitous defensive guard, it is
the same principle as `connector_flow.supports()` — a gesture that is not declared
is not silently mimicked.

⚠️ **The path stayed GENERIC even though it now serves only one connector**, and
that is deliberate: `/api/me/connectors/{name}/…` is the contract the dashboard calls,
it does not fall back to `/api/google/…` because the list shrank. The day a
second OAuth connector wants these verbs, a `declare_status` is all it needs.

**Constraint 1 (blocking, ruling of 04/09/2026) — `me.connector_status` does not create
a second truth.** Its state is derived from `access.status_for(sub)`, the SAME source
as `/api/me` (see `capabilities/me_account.py::_me`) — never a parallel call to
`google_oauth.list_accounts` that could diverge. That is why this file
imports NO `auth.*` module in the read path (`_status`): only
`_disconnect` (and the wrapper below) imports it, lazily, at call time.

⚠️ **google is multi-account, and `access.status_for` only carries ONE default
identity** (`ProviderStatus.identity_id`/`identity_label`, singular — a legacy of when
Google was single-account). The common contract below (`connected`, `set_at`,
`health_ko`, `health_reason`) therefore carries NOTHING google-specific beyond that: forcing
an `accounts` field here would fetch it from another read
(`google_oauth.list_accounts`), which is exactly the second truth that constraint 1
forbids. The multi-account richness keeps going through `connectors.identities`
(op=list) — outside this batch (option A explicitly ruled out, see oto-dashboard#125).

**Constraint 2 (blocking, Alexis's decision) — `me.connector_disconnect` is
irreversible, with no two-step.** A single call: revokes at the provider when the
mechanism allows it, and IN ALL CASES removes (or marks) the local row. The output
contract is `FederationDisconnected` (`ok`, `disconnected`), reused as is.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ... import access
from ...connectors import flow_status
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..federated_oauth import FederationDisconnected
from ..registry import CAPABILITIES


# --- Input / output ----------------------------------------------------------

class ConnectorOAuthStatusInput(BaseModel):
    name: str                                    # connector, from the path


class ConnectorOAuthDisconnectInput(BaseModel):
    name: str                                    # connector, from the path


class ConnectorOAuthStatus(BaseModel):
    """State of an OAuth consent — derived from `access.status_for` (constraint 1).
    `connected: false` with `set_at: null` is the normal state of an account that was
    never connected.

    `health_ko`/`health_reason` (oto#25 batch a) are `None` as long as nothing has been
    observed — never `False`: this contract cannot confirm good health,
    only report its rejection, once written (same policy as
    `connector_link.LinkState`)."""
    connected: bool
    set_at: Optional[str] = None
    health_ko: Optional[bool] = None
    health_reason: Optional[str] = None


def _require_oauth(name: str) -> None:
    """A connector that has not declared its verbs here is not silently mimicked —
    same principle as `connector_flow.supports()`. Today: google, and nobody
    else (atlassian and folkmcp left with the federation, ADR 0069)."""
    if not flow_status.supports(name):
        raise AuthzDenied(
            400, "no_oauth_status",
            f"“{name}” has no generic OAuth state: it is not one of the "
            "covered connectors (google). Its credential is read through "
            "`connectors.me` like the others.")


def _status(ctx: ResolvedCtx, inp: ConnectorOAuthStatusInput) -> dict:
    _require_oauth(inp.name)
    # SINGLE SOURCE (constraint 1): the SAME read as `/api/me`
    # (`capabilities/me_account.py::_me`), never a second call to `auth.*`.
    snapshot = access.status_for(ctx.sub)
    entry = (snapshot.get("providers") or {}).get(inp.name) or {}
    return {
        "connected": bool(entry.get("user_key_configured")),
        "set_at": entry.get("session_set_at"),
        "health_ko": entry.get("health_ko"),
        "health_reason": entry.get("health_reason"),
    }


async def _disconnect(ctx: ResolvedCtx, inp: ConnectorOAuthDisconnectInput) -> dict:
    _require_oauth(inp.name)
    return await flow_status.disconnect(inp.name, ctx)


# --- Wiring (IMPORT-TIME declaration, like `connector_flow.declare` — see
# `flow_status.py`). The `auth.*` import stays AT CALL TIME (in the wrapper), not here:
# this module mounts HTTP clients and reads its config at load. -----------------

def _google_disconnect(ctx: ResolvedCtx) -> dict:
    from ...auth import google as google_oauth
    # `account=None` = ALL of the sub's accounts, same behavior as
    # `federated_oauth._google_revoke` without a parameter — the generic gesture has no
    # notion of a named account (that one stays `connectors.identities`, outside this batch).
    google_oauth.revoke(ctx.sub, account=None)
    return {"ok": True, "disconnected": True}


flow_status.declare_status("google", disconnect=_google_disconnect)


CAPABILITIES += [
    Capability(
        key="me.connector_status",
        handler=_status,
        Input=ConnectorOAuthStatusInput,
        authz=SUB_ONLY,
        Output=ConnectorOAuthStatus,
        mcp=None,     # screen read gesture (dashboard); no useful agent counterpart
        errors=(DeclaredError(400, "no_oauth_status",
                              "this connector has no generic OAuth state "
                              "(other than google)"),),
        rest=RestBinding("GET", "/api/me/connectors/{name}/oauth-status"),
        description=("Is my OAuth consent for this connector (google) set, "
                     "and since when — derived from the same source as `/api/me`. "
                     "`connected: false` with `set_at: null` is the normal state of an "
                     "account that was never connected."),
    ),
    Capability(
        key="me.connector_disconnect",
        handler=_disconnect,
        Input=ConnectorOAuthDisconnectInput,
        authz=SUB_ONLY,
        Output=FederationDisconnected,
        mcp=None,
        errors=(DeclaredError(400, "no_oauth_status",
                              "this connector has no generic OAuth state "
                              "(other than google)"),),
        rest=RestBinding("DELETE", "/api/me/connectors/{name}/oauth"),
        description=("Revokes my OAuth consent for this connector (google) — "
                     "at the provider when the mechanism "
                     "allows it, and in all cases removes the local row. A SINGLE "
                     "call, irreversible: never an intermediate state waiting for "
                     "confirmation. Idempotent: `disconnected: false` means there was "
                     "nothing to remove, not that the removal failed."),
    ),
]
