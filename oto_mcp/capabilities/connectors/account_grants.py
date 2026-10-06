"""Shared connector account authorization (otomata-private#55) — OWNER's
surface: grant / revoke to a named user (OR an entire group, extension
2026-09) the right to operate THEIR Unipile account on a channel (multi-client agency,
org account operated by a team, external freelancer). **Cross-org by design**: the
grantee does NOT need to share an org with the owner — one shares one's OWN
account, with whomever one wants.

`grantee` = a user's sub OR email, OR `group:<id>` for a group — in which case
ALL ITS CURRENT MEMBERS operate the account, in DYNAMIC fan-out (membership
is re-read live on every call, never a list frozen at grant time: joining or
leaving the group changes access without an individual re-lending). The original issue (#55)
already asked for « named members OR a department » — the group had never been
delivered (ADR 0051 had left it orthogonal to instance sharing, without settling the
target of the grant itself).

Deny-by-default, revocation with immediate effect (the grant is revalidated on every call
in resolution, see `connector_identities.resolve_operated_account_id`), audited
(`granted_by`/`granted_at`). `SUB_ONLY` authz: « reserved to the owner » is
guaranteed BY CONSTRUCTION — `owner_sub := ctx.sub`, never accepted from a client param
(same structural lock as the `org_id` injection of the combinators). No org_admin
escalation: only the account owner grants (requirement #55) — true for a group
target as for a named user.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ... import db, group_store
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

Channel = Literal["linkedin", "whatsapp", "telegram", "instagram"]


def _provider_for(channel: str) -> str:
    """Front channel → DB provider (single source: `tools/unipile.UNIPILE_CHANNELS`).
    Lazy import — no module-level dependency capabilities → runtime tools."""
    from ...tools.unipile import UNIPILE_CHANNELS
    return UNIPILE_CHANNELS[channel]


def _parse_group_target(grantee: str) -> Optional[int]:
    """A `grantee` in the format `group:<id>` targets a group rather than a user — same
    cross-org permissiveness as the individual grantee (no requirement that the
    owner be a member of the named group themselves: they share THEIR OWN
    account, with whom/what they like). None if `grantee` does not have this form."""
    if not grantee.startswith("group:"):
        return None
    raw = grantee[len("group:"):]
    if not raw.isdigit():
        raise AuthzDenied(400, "invalid_group_target",
                          f"Invalid group target: {grantee!r} (expected group:<id>)")
    return int(raw)


def _un_seul_porteur(email: str) -> Optional[dict]:
    """The record of the account bearing this address — or a REFUSAL if it designates
    several. `None` when nobody bears it: the caller decides (the grant
    raises 404, the revocation tolerates it and falls back to the supplied string).

    ⚠️ An address does not designate one account. Two accounts can bear it — ours
    and a tenant's, or two of ours (measured: ten addresses, twenty
    accounts, including a pair with no tenant at all). Silently picking one is
    granting access to one's connector account to the wrong recipient, or believing
    one has withdrawn it from the right one. `grantee` ALREADY accepts a sub: the refusal therefore has
    an immediate way out, and it names it.
    """
    porteurs = db.get_users_by_email(email)
    if len(porteurs) > 1:
        subs = ", ".join(f"`{u['sub']}`" for u in porteurs)
        raise AuthzDenied(
            400, "ambiguous_email",
            f"The address `{email}` designates {len(porteurs)} accounts: {subs}. "
            "Retry with the `sub` of the one you mean — `grantee` accepts it.")
    return porteurs[0] if porteurs else None


def _resolve_grantee(ctx: ResolvedCtx, grantee: str) -> dict:
    """`grantee` = sub OR email → user record. The owner shares THEIR OWN
    account (owner := ctx.sub by construction) → they can grant it to ANY
    oto user, **including outside their orgs** (cross-org by design: agency /
    external freelancer). The only safeguards: the user must exist, and no
    self-grant (you already operate your account)."""
    if "@" in grantee:
        user = _un_seul_porteur(grantee)
    else:
        user = db.get_user(grantee)
    if not user:
        raise AuthzDenied(404, "unknown_user", f"Unknown user: {grantee}")
    if user["sub"] == ctx.sub:
        raise AuthzDenied(400, "self_grant", "You already operate your own account.")
    return user


class AccountGrantsListInput(BaseModel):
    pass


class AccountGrantInput(BaseModel):
    channel: Channel
    grantee: str                         # sub, email, or `group:<id>` (live fan-out)


class GrantedByMe(BaseModel):
    """An authorization that I GRANTED: « so-and-so — or a whole group — may
    operate my account on this channel ». Exactly one of the two sets
    (`grantee_sub`/`grantee_email`/`grantee_name`) or (`grantee_group_id`/
    `grantee_group_name`) is filled in, depending on the grant's target."""
    # ⚠️ `provider` is NOT the entry's `channel`: it is the DB provider, in
    # UPPERCASE (`LINKEDIN`, `WHATSAPP`…). One grants by `channel=linkedin` and
    # reads back `provider="LINKEDIN"` — a client that compares the two as is never
    # matches.
    provider: str
    # LIVE state of the account (LEFT JOIN), not the grant's audit snapshot: `null`
    # if the channel has been disconnected since — the grant still exists but is INERT.
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    grantee_sub: Optional[str] = None
    grantee_email: Optional[str] = None     # null if the user has no `users` row
    grantee_name: Optional[str] = None
    grantee_group_id: Optional[int] = None
    grantee_group_name: Optional[str] = None
    granted_by: Optional[str] = None
    granted_at: Optional[str] = None
    # DERIVED from `account_id IS NOT NULL`: `false` = I disconnected the channel, the
    # grant sleeps. It is neither a revocation nor an error — reconnecting
    # resurrects it as is.
    active: bool


class GrantedToMe(BaseModel):
    """An authorization that I RECEIVED: someone else's account that I can operate."""
    provider: str                           # DB provider in UPPERCASE (see GrantedByMe)
    owner_sub: str
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    account_id: Optional[str] = None        # null = the owner disconnected the channel
    account_name: Optional[str] = None
    # The org under which the OWNER connected this account — says WHERE the
    # share comes from. The grant itself is not scoped to any org (cross-org by design): this is
    # therefore not an access filter.
    owner_org_id: Optional[int] = None
    owner_org_name: Optional[str] = None
    granted_at: Optional[str] = None
    active: bool                            # false: channel disconnected OR lender paused
    # The owner is PAUSED (#898): the loan is suspended with them, not revoked,
    # and resumes as is on their wake-up. Without this field, `active=false` would read
    # « disconnected » and point to an owner who can do nothing about it.
    owner_suspended: bool = False
    # None = named grant. Otherwise, the group whose membership CARRIES this access
    # (dynamic fan-out — leaving the group makes it vanish on the next call).
    via_group_id: Optional[int] = None
    via_group_name: Optional[str] = None


class AccountGrants(BaseModel):
    """The two faces of connector account sharing (#55), from the caller's
    point of view. Deny-by-default: two empty lists = nobody operates anything."""
    granted_by_me: list[GrantedByMe]
    granted_to_me: list[GrantedToMe]


class AccountGrantCreated(BaseModel):
    """Echo of a granted authorization. Exactly one of `grantee_sub` (user
    target) or `grantee_group_id` (group target) is filled in."""
    ok: bool
    channel: str                            # the FRONT channel as passed (lowercase)
    account_id: str                         # the targeted account, snapshot at grant time
    grantee_sub: Optional[str] = None       # RESOLVED sub (the input could be an email)
    grantee_email: Optional[str] = None
    grantee_group_id: Optional[int] = None
    grantee_group_name: Optional[str] = None
    # Documented limitation, returned as is: the grant authorizes, it does not
    # supply the key. The beneficiary(ies) must still reach this account
    # with THEIR key (shared org/platform = OK; a personal BYO key does not see it
    # → 404 at call time).
    note: str


class AccountGrantRevoked(BaseModel):
    """Echo of a revocation. Idempotent: `revoked=false` = there was no
    grant to withdraw, not a refusal. Exactly one of `grantee_sub`/`grantee_group_id`
    is filled in, depending on the target passed as input."""
    ok: bool
    channel: str
    # ⚠️ Echo of the input when it could not be resolved: an UNKNOWN email
    # is returned as is here (no error — the withdrawal merely finds nothing,
    # where `grant` would have raised a 404). A `grantee_sub` containing an
    # « @ » + `revoked:false` is therefore the sign of a badly named target, not of a
    # grant already withdrawn.
    grantee_sub: Optional[str] = None
    grantee_group_id: Optional[int] = None
    revoked: bool


def _list(ctx: ResolvedCtx, inp: AccountGrantsListInput) -> dict:
    return {
        "granted_by_me": (db.list_account_grants_by_owner(ctx.sub)
                          + db.list_account_group_grants_by_owner(ctx.sub)),
        "granted_to_me": db.list_account_grants_to(ctx.sub),
    }


def _grant(ctx: ResolvedCtx, inp: AccountGrantInput) -> dict:
    provider = _provider_for(inp.channel)
    # Member scope (ADR 0033): the owner's account lives in THEIR context
    # org — `ctx.org_id` is injected by SUB_ONLY (= access.current_org).
    account_id = db.get_unipile_account_id(ctx.sub, ctx.org_id, provider)
    if not account_id:
        raise AuthzDenied(404, "channel_not_connected",
                          f"You have no {inp.channel} account connected — connect it "
                          "first (dashboard, connector card).")
    group_id = _parse_group_target(inp.grantee)
    if group_id is not None:
        group = group_store.get_group(group_id)
        if not group:
            raise AuthzDenied(404, "unknown_group", f"Unknown group: {inp.grantee}")
        db.set_account_group_grant(ctx.sub, provider, account_id, group_id,
                                   granted_by=ctx.sub)
        return {
            "ok": True, "channel": inp.channel, "account_id": account_id,
            "grantee_group_id": group_id, "grantee_group_name": group["name"],
            "note": "Every CURRENT member of the group operates this account via the "
                    "identity selector (oto_identity op=set) or a project "
                    "pin — access follows group membership, live.",
        }
    user = _resolve_grantee(ctx, inp.grantee)
    db.set_account_grant(ctx.sub, provider, account_id, user["sub"], granted_by=ctx.sub)
    return {
        "ok": True, "channel": inp.channel, "account_id": account_id,
        "grantee_sub": user["sub"], "grantee_email": user.get("email"),
        # Documented limitation: the grantee's key must reach this account (shared
        # org/platform key = OK; owner on a personal BYO key ≠ 404 at call time).
        "note": "The authorized member operates this account via the identity selector "
                "(oto_identity op=set) or a project pin.",
    }


def _revoke(ctx: ResolvedCtx, inp: AccountGrantInput) -> dict:
    provider = _provider_for(inp.channel)
    group_id = _parse_group_target(inp.grantee)
    if group_id is not None:
        revoked = db.clear_account_group_grant(ctx.sub, provider, group_id)
        db.clear_operated_pointers_to_group(ctx.sub, provider, group_id)
        return {"ok": True, "channel": inp.channel, "grantee_group_id": group_id,
                "revoked": revoked}
    if "@" in inp.grantee:
        # ⚠️ Revocation too: on an ambiguous address, revoking « one of the two »
        # leaves access to the second — and the owner believes they withdrew it. The
        # refusal is therefore the same here as for the grant, for the opposite reason.
        user = _un_seul_porteur(inp.grantee)
        grantee_sub = user["sub"] if user else inp.grantee
    else:
        grantee_sub = inp.grantee
    revoked = db.clear_account_grant(ctx.sub, provider, grantee_sub)
    # Hygiene: clear the grantee's pointer if they were operating this account. The backstop
    # does NOT rely on it (grant re-checked on every call).
    db.clear_operated_pointers_to(ctx.sub, provider, grantee_sub)
    return {"ok": True, "channel": inp.channel, "grantee_sub": grantee_sub,
            "revoked": revoked}


CAPABILITIES += [
    Capability(
        key="connectors.account_grants.list", handler=_list, Input=AccountGrantsListInput,
        authz=SUB_ONLY, Output=AccountGrants,
        description="List the connector account authorizations you granted (who may operate "
                    "your Unipile accounts, per channel) and those granted to you (accounts "
                    "you may operate). Deny-by-default: no grant = nobody but the owner.",
        rest=RestBinding("GET", "/api/me/connector-accounts/grants"),
    ),
    Capability(
        key="connectors.account_grants.grant", handler=_grant, Input=AccountGrantInput,
        authz=SUB_ONLY, Output=AccountGrantCreated,
        description="[account owner] Authorize an oto user OR a whole group (grantee = email/sub, "
                    "or `group:<id>` for every CURRENT member, dynamically — including someone or "
                    "a group OUTSIDE your orgs, e.g. an external freelancer or agency) to OPERATE "
                    "your connected account on a channel (linkedin, whatsapp, …), acting as you. "
                    "Only the owner can grant; revocable anytime with immediate effect; audited.",
        rest=RestBinding("POST", "/api/me/connector-accounts/{channel}/grants"),
    ),
    Capability(
        key="connectors.account_grants.revoke", handler=_revoke, Input=AccountGrantInput,
        authz=SUB_ONLY, Output=AccountGrantRevoked,
        description="[account owner] Revoke a member's (or a group's, `group:<id>`) authorization "
                    "to operate your account on a channel. Immediate: the next call under your "
                    "identity fails explicitly. Idempotent.",
        rest=RestBinding("DELETE", "/api/me/connector-accounts/{channel}/grants"),
    ),
]
