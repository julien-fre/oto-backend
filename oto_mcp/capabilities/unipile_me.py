"""Unipile hosted messaging, MEMBER side: connect, reconcile, read, unlink.

Four hand-written routes until 2026-08-27, ported to capabilities (ADR 0009) —
same paths, same codes, same body on the wire:

- `POST   /api/me/unipile/connect`   → hosted-auth URL (or adoption of an already-linked account)
- `POST   /api/me/unipile/reconcile` → explicit poll-and-bind
- `GET    /api/me/unipile`           → per-user status, with opportunistic self-heal
- `DELETE /api/me/unipile`           → soft-disconnect WITHIN the current org

⚠️ **`/api/me/unipile/connect` is SUPERSEDED and lives until the front end switches over.** Its
successor is the generic capability `me.connector_connect`
(`POST /api/me/connectors/{name}/connect`), which returns a `FlowStart` — the shared body
already lives in `unipile_connect.hosted_auth_url`, called by both. We still port it
as a capability: debt gets repaid, and its future removal becomes one line.

⚠️ **There is no linking webhook any more**: `POST /api/unipile/webhook` was removed on
2026-08-29 (#581) — the provider had stopped calling this callback since its v2, and an
unauthenticated route with no legitimate caller gets removed. Linking goes through the
reconciliation below, under the person's JWT.

**No MCP face** (`mcp=None`). The agent face of this gesture already exists and is
`me.connector_connect`; adding a second one here would recreate, on the MCP side, exactly
the duplicate this work removes on the REST side.

⚠️ **Two fallbacks that look like bugs and are not:**
- `GET /api/me/unipile` **reconciles** before answering (it is THE linking path) —
  best-effort, never fatal for the status, and a no-op without a pending, hence with no
  network call;
- `DELETE` is a **soft**-disconnect of the BINDING (the row survives as proof of
  ownership) and, for a seat of the PLATFORM key that no other org still uses, the
  account is then DELETED at Unipile so it stops billing (`_free_platform_seat`).
  It is per-ORG, like the display: what you see is what you disconnect.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from pydantic import BaseModel

from .. import access, db
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_ME = "/api/me/unipile"


# --- Inputs -----------------------------------------------------------------

class UnipileConnectInput(BaseModel):
    channel: str = "linkedin"
    # Override the cross-org anti-duplicate refusal (#172): reconnecting a login already
    # held under ANOTHER org's key would create a second account.
    force: bool = False
    # 'recruiter' | 'sales_navigator' — LinkedIn product to ACTIVATE at connection,
    # without which these APIs answer 403 (classic only).
    premium: Optional[str] = None
    # Originating front end: without it, the end of the wizard goes back to oto-dashboard whatever
    # the product that requested the connection. The dashboard does not send it and
    # therefore falls back on its own destination, unchanged.
    app: Optional[str] = None


class UnipileReconcileInput(BaseModel):
    """We link what THIS account has just connected. `account_id` (optional) = the one
    Unipile appended to the return address: it RESTRICTS the linking to that account,
    without lifting any guard — a forged identifier can only designate an account that
    the selection could have retained anyway."""
    account_id: Optional[str] = None


class UnipileStatusInput(BaseModel):
    """No parameters: the status is that of the token holder, in their active org."""


class UnipileDisconnectInput(BaseModel):
    # Front-end channel ('linkedin', 'whatsapp'…); UPPERCASED for the vault.
    channel: str = "linkedin"


# --- Outputs ----------------------------------------------------------------

class UnipileChannel(BaseModel):
    connected: bool
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    connected_at: Optional[str] = None


class UnipileElsewhere(BaseModel):
    """An account of MINE, connected under ANOTHER org with the same platform key: it is
    adoptable here in one click — the "Connect" button adopts it on the backend side, with no wizard."""
    account_id: str
    account_name: Optional[str] = None
    org_id: Optional[int] = None


class UnipileStatusView(BaseModel):
    """⚠️ **`channels` only lists the accounts linked to the CURRENT org.** The binding is an
    act per org (explicit model): a channel seen as "disconnected" here may be connected
    elsewhere — that is precisely what `elsewhere` says, and why a cross-org
    resurgence is no longer possible.

    `subscribed` is the gate of the "connect" button: true if the org brings its own
    key (BYO) or if the hosted messaging option has been granted to it. `mode` gives
    its ORIGIN (`user`|`group`|`org`|`platform`|`over_quota`|`forbidden`)."""
    subscribed: bool
    mode: Optional[str] = None
    byo: bool
    channels: dict[str, UnipileChannel]
    elsewhere: dict[str, UnipileElsewhere]


class UnipileConnectView(BaseModel):
    """⚠️ **Two outcomes, two shapes.** The ordinary case returns `{url}`: the consent
    page to open. The ADOPTION case returns `{adopted, channel, account_name}` and
    **no `url`** — the account was already connected under this identity in another
    org, it has just been attached here, there is no consent to give. The front end
    must refresh rather than open a window."""
    url: Optional[str] = None
    adopted: Optional[bool] = None
    channel: Optional[str] = None
    account_name: Optional[str] = None


class UnipileReconcileView(BaseModel):
    """`bound: false` with `accounts: []` = nothing to link, not an outage (no pending).

    When nothing was linked, `reason` carries the established cause (`no_pending`,
    `no_candidate`, `candidates_dead`, `ambiguous_candidates`, `no_credential`,
    `provider_unreachable`) and `detail` the sentence that explains it.
    `ambiguous_candidates` (oto#247): without `account_id`, several accounts
    connected in the same window on the shared key may be mine — nothing
    is linked until the return address's `account_id` is passed again. A tenant's front end displays them on return from the
    hosted flow: it is the only surface where `no_candidate` stops being silent."""
    bound: bool
    accounts: list[Any]
    reason: Optional[str] = None
    detail: Optional[str] = None


class UnipileDisconnected(BaseModel):
    ok: bool


# --- Handlers ---------------------------------------------------------------

async def _connect(ctx: ResolvedCtx, inp: UnipileConnectInput) -> dict:
    """Unipile hosted-auth: generates the URL where the user connects THEIR account under
    the shared subscription (their org's key). Per-user (not admin)."""
    # Body shared by REST + MCP (`unipile_connect_start`): gates + nonce +
    # hosted_auth_link live in `unipile_connect`.
    from .. import unipile_connect
    try:
        out = await unipile_connect.hosted_auth_url(
            ctx.sub, str(inp.channel or "linkedin"),
            force=bool(inp.force),
            premium=(str(inp.premium).strip().lower() if inp.premium else None),
            app=(str(inp.app) if inp.app else None))
    except unipile_connect.ConnectRefused as e:
        # ⚠️ HISTORICAL shape kept: 502 (upstream failure) and 409 (cross-org duplicate,
        # #172) carry an actionable message, served INSTEAD OF the machine code in
        # `error`; the others expose their code. The `error` field is therefore PROSE
        # for these two — which is what has always been served.
        if e.status in (409, 502):
            raise AuthzDenied(e.status, e.message)
        # The sentence goes out with the code (oto#108): `unipile_option_required` alone says
        # neither who grants the option nor where — the message does.
        raise AuthzDenied(e.status, e.code, e.message)
    # Adoption (per-org binding): the account connected elsewhere was linked HERE without
    # a wizard → no URL, the front end refreshes ({adopted, account_name, channel}).
    if out.get("adopted"):
        return out
    return {"url": out["url"]}


async def _reconcile(ctx: ResolvedCtx, inp: UnipileReconcileInput) -> dict:
    """Explicit poll-and-bind (v2 webhook not delivered): links the account that `sub` has just
    connected. The dashboard may call it on return from hosted-auth. Idempotent."""
    from .. import unipile_connect
    hint = (inp.account_id or "").strip()
    if hint:
        return await asyncio.to_thread(unipile_connect.reconcile_pending, ctx.sub, hint)
    return await asyncio.to_thread(unipile_connect.reconcile_pending, ctx.sub)


async def _status(ctx: ResolvedCtx, inp: UnipileStatusInput) -> dict:
    """Per-user connection status. **Self-heal**: since the v2 hosted-auth webhook is not
    delivered, we reconcile (poll-and-bind) freshly connected accounts when the status
    loads — a no-op without a pending (hence no Unipile call). Best-effort:
    never fatal for the status."""
    from .. import unipile_connect
    try:
        await asyncio.to_thread(unipile_connect.reconcile_pending, ctx.sub)
    except Exception:  # noqa: BLE001 — opportunistic reconciliation, never blocking
        logger.warning("unipile status: best-effort reconcile failed", exc_info=True)
    from ..tools import unipile
    return unipile.status_for(ctx.sub)


def _platform_seat_here(sub: str, org_id: Optional[int], provider: str) -> Optional[str]:
    """The `account_id` of `sub`'s LIVE binding on this channel in this org when it holds
    a seat of the PLATFORM key, else None. A BYO account (`platform_seat` false) lives on
    the org's own Unipile subscription: never ours to delete."""
    if org_id is None:
        return None
    for a in db.list_unipile_accounts(sub):
        if (a.get("org_id") == org_id and (a.get("provider") or "").upper() == provider
                and a.get("platform_seat")):
            return a.get("account_id")
    return None


def _free_platform_seat(account_id: str) -> str:
    """Delete the account at Unipile once NO live binding names it any more — the
    platform key pays for a seat as long as it EXISTS there, used or not. Called after
    the soft-disconnect, never before (`unipile_seats.liberer`: unlink first, delete
    after). An account adopted in another org stays: that org still uses it.

    Best-effort: the person's disconnect has already happened and must not fail on an
    upstream outage. A seat left behind shows as `disconnected` in the platform seat
    inventory (`oto_admin_unipile_seat`), which an admin can release. Returns what
    happened: `released` | `in_use_elsewhere` | `no_platform_key` | `failed`."""
    if any(r.get("account_id") == account_id for r in db.unipile_account_owners()):
        return "in_use_elsewhere"
    from . import unipile_seats
    try:
        client = unipile_seats._platform_client()
        if client is None:
            logger.warning("unipile disconnect: no platform key, seat %s left at the provider",
                           account_id)
            return "no_platform_key"
        client.delete_account(account_id)
    except Exception:  # noqa: BLE001 — the disconnect stands; the inventory shows the seat
        logger.warning("unipile disconnect: deleting seat %s at the provider failed — it "
                       "stays `disconnected` in the seat inventory", account_id, exc_info=True)
        return "failed"
    logger.info("unipile disconnect: seat %s deleted at the provider", account_id)
    return "released"


def _disconnect(ctx: ResolvedCtx, inp: UnipileDisconnectInput) -> dict:
    """SOFT-disconnects the channel WITHIN THIS ORG (the row survives as proof of
    ownership), then, for a seat of the platform key that no other org still uses,
    deletes the account at Unipile so it stops billing — reconnecting later is a full
    provider login and yields a NEW account_id, which reconciliation binds by its
    creation date like any first connection.
    Per-org: the binding is an act per org — and since the display shows ONLY the
    current org's bindings, what you see is what you disconnect (ex-#221)."""
    provider = str(inp.channel or "linkedin").upper()
    org_id = access.current_org(ctx.sub)
    seat = _platform_seat_here(ctx.sub, org_id, provider)
    db.clear_unipile_account(ctx.sub, org_id, provider)
    if seat:
        _free_platform_seat(seat)
    return {"ok": True}


_DOC_CONNECT = (
    "Starts the connection of a hosted messaging account under my org's "
    "subscription. Returns `{url}`: the consent page to open. ⚠️ Second possible outcome — "
    "`{adopted: true, channel, account_name}` **with no url**: the account was already "
    "connected under my identity in another org and has just been attached here, there is "
    "nothing to consent to, you must refresh. `premium` activates a LinkedIn product "
    "(`recruiter`, `sales_navigator`) without which these APIs answer 403."
)
_DOC_RECONCILE = (
    "Explicitly links the account I have just connected (poll-and-bind), to be called on "
    "return from consent with the `account_id` carried by the return address: without "
    "it, nothing is linked when several connections in the same window may be "
    "mine (`reason: ambiguous_candidates`). Idempotent. `bound: false` with "
    "`accounts: []` means \"nothing to link\", not \"outage\"."
)
_DOC_STATUS = (
    "The state of my hosted messaging WITHIN THE CURRENT ORG: connected channels, origin of "
    "the key, and option unlocked or not. ⚠️ `channels` only shows accounts linked to "
    "THIS org — a channel seen as disconnected may be connected elsewhere, and `elsewhere` "
    "then says so, with what is adoptable here in one click."
)
_DOC_DISCONNECT = (
    "Unlinks a channel FROM THIS ORG. The row survives as proof of ownership; an "
    "account on the PLATFORM key that no other org still uses is then deleted at the "
    "provider, so it stops billing — reconnecting is a full new login. What is "
    "displayed is what is unlinked — never a binding of another org."
)

CAPABILITIES += [
    Capability(
        key="me.unipile.connect", handler=_connect, Input=UnipileConnectInput,
        authz=SUB_ONLY, Output=UnipileConnectView, description=_DOC_CONNECT,
        mcp=None,   # the agent face is `me.connector_connect` — no second path
        rest=RestBinding("POST", _ME + "/connect"),
    ),
    Capability(
        key="me.unipile.reconcile", handler=_reconcile, Input=UnipileReconcileInput,
        authz=SUB_ONLY, Output=UnipileReconcileView, description=_DOC_RECONCILE,
        mcp=None,
        rest=RestBinding("POST", _ME + "/reconcile"),
    ),
    Capability(
        key="me.unipile.status", handler=_status, Input=UnipileStatusInput,
        authz=SUB_ONLY, Output=UnipileStatusView, description=_DOC_STATUS,
        mcp=None,
        rest=RestBinding("GET", _ME),
    ),
    Capability(
        key="me.unipile.disconnect", handler=_disconnect,
        Input=UnipileDisconnectInput, authz=SUB_ONLY, Output=UnipileDisconnected,
        description=_DOC_DISCONNECT,
        mcp=None,
        rest=RestBinding("DELETE", _ME),
    ),
]
