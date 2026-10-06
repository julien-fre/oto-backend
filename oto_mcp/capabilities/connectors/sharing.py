"""Instance SHARING capability (ADR 0044) — WRITE surface of `share_side`.

`share_side` = EXTENSION: a member lends THEIR instance (their key in the current org)
to a named peer. The beneficiary then uses it by pinning the instance (`_instance=`); the
guard `access.guard_instance_access` authorizes the beneficiary (borrows the key, keeps
their OWN org context — cross-org OK, the named loan is the consent).
Owner-scoped: `SUB_ONLY`, the handler touches ONLY the caller's vault row
(`member_id(current org, sub)`) → you only ever lend your own key.

⚠️ `share_down` (RESTRICTING an org/team shared key) is the OPPOSITE axis (deny-by-
default) — distinct write surface, not exposed here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ... import access, credentials_store, db, providers
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES


class LendInstanceInput(BaseModel):
    connector: str
    to: str = Field(description="sub of the peer to lend to (or to revoke the loan from)")
    account: str = ""
    revoke: bool = False


class LendInstanceResult(BaseModel):
    """State of the loan AFTER the operation — not just an acknowledgment of the action."""
    ok: bool
    connector: str
    revoked: bool                           # echo of the intent (`revoke` from the input)
    # ⚠️ The COMPLETE list of borrowers afterwards, not just the targeted peer: a
    # `revoke` therefore returns a non-empty list if other loans remained. And it
    # carries ONLY the named `user:` entries — a `share_side` targeting a
    # TEAM exists in the vault but doesn't appear here (this surface only lends
    # to people). An empty `lent_to` doesn't prove the instance isn't
    # shared with anyone.
    lent_to: list[str]                      # borrowers' subs


def _lend_instance(ctx: ResolvedCtx, inp: LendInstanceInput) -> dict:
    if providers.connector_for_provider(inp.connector) is None:
        raise AuthzDenied(400, "unknown_connector", f"Unknown connector `{inp.connector}`.")
    org = access.current_org(ctx.sub)
    if org is None:
        raise AuthzDenied(400, "no_active_org",
                          "No active org — unable to lend an instance.")
    if inp.to == ctx.sub:
        raise AuthzDenied(400, "self_lend", "Lending to yourself makes no sense.")
    if db.get_user(inp.to) is None:
        raise AuthzDenied(404, "unknown_user", f"Unknown user `{inp.to}`.")
    eid = credentials_store.member_id(org, ctx.sub)
    _, side = credentials_store.get_instance_sharing(
        credentials_store.MEMBER, eid, inp.connector, inp.account)
    entry = f"user:{inp.to}"
    side = list(side or [])
    if inp.revoke:
        side = [s for s in side if s != entry]
    elif entry not in side:
        side.append(entry)
    ok = credentials_store.set_instance_sharing(
        credentials_store.MEMBER, eid, inp.connector, inp.account, share_side=side)
    if not ok:
        raise AuthzDenied(404, "no_instance",
                          f"No `{inp.connector}` instance set in this org — "
                          f"nothing to lend (configure your key first).")
    return {"ok": True, "connector": inp.connector, "revoked": inp.revoke,
            "lent_to": [s[len("user:"):] for s in side if s.startswith("user:")]}


CAPABILITIES += [
    Capability(
        key="connectors.lend_instance", handler=_lend_instance, Input=LendInstanceInput,
        authz=SUB_ONLY, Output=LendInstanceResult,
        description=("Lend YOUR connector instance (your key in your current org) to a "
                     "peer so they can use it by pinning it (instance=). `to`=peer's sub; "
                     "`revoke=true` takes it back. You only ever share your OWN key; the "
                     "borrower operates under THEIR own org context (ADR 0044 share_side)."),
        rest=RestBinding("POST", "/api/me/connectors/{connector}/lend", {"connector": "connector"}),
    ),
]
