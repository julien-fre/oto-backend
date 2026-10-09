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

from typing import Optional

from pydantic import BaseModel, Field

from ... import access, credentials_store, db, org_store, providers
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES


class LendInstanceInput(BaseModel):
    connector: str = Field(description="the connector — a service card (`outlook`, "
                                       "`gmail`…) lends the account it borrows")
    to: str = Field(description="the email or the sub of the peer to lend to (or to "
                                "revoke the loan from) — an email must be a member of "
                                "your current organization")
    account: str = Field("", description="which of your accounts — optional when you "
                                         "have only one")
    revoke: bool = False


class LendInstanceResult(BaseModel):
    """State of the loan AFTER the operation — not just an acknowledgment of the action."""
    ok: bool
    # The connector whose vault row is lent: the CARRIER when a service card was named
    # (`outlook` → `microsoft`), since a service owns no instance (`credential_of`).
    connector: str
    account: str = ""                       # the account lent (`""` = single-key connector)
    # Present when the loan reaches beyond the named card: lending a carrier's account
    # lends every service that account authorized — said, never implied.
    note: Optional[str] = None
    revoked: bool                           # echo of the intent (`revoke` from the input)
    # ⚠️ The COMPLETE list of borrowers afterwards, not just the targeted peer: a
    # `revoke` therefore returns a non-empty list if other loans remained. And it
    # carries ONLY the named `user:` entries — a `share_side` targeting a
    # TEAM exists in the vault but doesn't appear here (this surface only lends
    # to people). An empty `lent_to` doesn't prove the instance isn't
    # shared with anyone.
    lent_to: list[str]                      # borrowers' subs


_ACCEPTED_TO = "`to` = the email or the sub of a member of your organization"


def _borrower_sub(org: int, to: str) -> str:
    """The borrower's sub. An EMAIL resolves among the members of the lender's CURRENT
    org only — never through the platform directory: an email outside the org gets
    the same refusal whether or not an oto account carries it elsewhere. A sub is
    taken as is (a named loan may cross orgs, ADR 0044)."""
    to = (to or "").strip()
    if "@" in to:
        member = org_store.get_org_member_by_email(org, to)
        if member is None:
            raise AuthzDenied(404, "not_an_org_member",
                              f"`{to}` is not the email of a member of your organization "
                              f"(org #{org}): only a member can borrow by email — "
                              f"{_ACCEPTED_TO}.")
        return member["sub"]
    if db.get_user(to) is None:
        raise AuthzDenied(404, "unknown_user", f"Unknown user `{to}`: {_ACCEPTED_TO}.")
    return to


def _lent_account(eid: str, connector: str, account: str) -> str:
    """The account of the lender's row to lend: the one named, otherwise the only one.
    Several and none named = a refusal that lists them; none at all = no instance."""
    accounts = [r["account"] for r in credentials_store.list_accounts(
        credentials_store.MEMBER, eid, connector)]
    if account and account in accounts:
        return account
    if not accounts:
        raise AuthzDenied(404, "no_instance",
                          f"No `{connector}` instance set in this org — nothing to lend "
                          f"(configure your key first).")
    listed = ", ".join(f"`{a}`" for a in accounts)
    if account:
        raise AuthzDenied(404, "unknown_account",
                          f"You have no `{connector}` account `{account}` in this org: "
                          f"pass `account=`: one of {listed}.")
    if len(accounts) == 1:
        return accounts[0]
    raise AuthzDenied(400, "account_required",
                      f"You have several `{connector}` accounts: pass `account=`: one of "
                      f"{listed}.")


def _reach_note(named: str, carrier: str, account: str) -> Optional[str]:
    """What the loan really covers, when it is more than the named card: the row of a
    carrier serves every service that borrows it (`credential_of`)."""
    services = sorted(c.name for c in providers.REGISTRY.values()
                      if c.credential_of == carrier)
    if not services:
        return None
    label = providers.REGISTRY[carrier].label
    via = (f"`{named}` has no instance of its own, it borrows your {label}. "
           if named != carrier else "")
    return (f"{via}This lends your {label} `{account}` with every service it authorized "
            f"(among {', '.join(services)}), not only one card.")


def _lend_instance(ctx: ResolvedCtx, inp: LendInstanceInput) -> dict:
    if providers.connector_for_provider(inp.connector) is None:
        raise AuthzDenied(400, "unknown_connector", f"Unknown connector `{inp.connector}`.")
    # A service card owns no vault row: the loan is the carrier's (`credential_of`).
    connector = providers.credential_provider(inp.connector)
    org = access.current_org(ctx.sub)
    if org is None:
        raise AuthzDenied(400, "no_active_org",
                          "No active org — unable to lend an instance.")
    to = _borrower_sub(org, inp.to)
    if to == ctx.sub:
        raise AuthzDenied(400, "self_lend", "Lending to yourself makes no sense.")
    eid = credentials_store.member_id(org, ctx.sub)
    account = _lent_account(eid, connector, inp.account)
    _, side = credentials_store.get_instance_sharing(
        credentials_store.MEMBER, eid, connector, account)
    entry = f"user:{to}"
    side = list(side or [])
    if inp.revoke:
        side = [s for s in side if s != entry]
    elif entry not in side:
        side.append(entry)
    ok = credentials_store.set_instance_sharing(
        credentials_store.MEMBER, eid, connector, account, share_side=side)
    if not ok:   # removed between the listing and this write
        raise AuthzDenied(404, "no_instance",
                          f"No `{connector}` instance set in this org — "
                          f"nothing to lend (configure your key first).")
    return {"ok": True, "connector": connector, "account": account,
            "note": _reach_note(inp.connector, connector, account),
            "revoked": inp.revoke,
            "lent_to": [s[len("user:"):] for s in side if s.startswith("user:")]}


CAPABILITIES += [
    Capability(
        key="connectors.lend_instance", handler=_lend_instance, Input=LendInstanceInput,
        authz=SUB_ONLY, Output=LendInstanceResult,
        description=("Lend YOUR connector instance (your key in your current org) to a "
                     "peer so they can use it by pinning it (instance=). `to` = the email "
                     "or the sub of a member of your organization; `account` is optional "
                     "when you have only one. A service card (`outlook`, `gmail`…) lends "
                     "the account it borrows — with every service that account authorized; "
                     "`revoke=true` takes it back. You only ever share your OWN key; the "
                     "borrower operates under THEIR own org context (ADR 0044 share_side)."),
        rest=RestBinding("POST", "/api/me/connectors/{connector}/lend", {"connector": "connector"}),
    ),
]
