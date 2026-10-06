"""Who SEES a connector instance — R9, decided by Alexis on 2026-08-27.

**The verdict.** « Visibility is a property of the INSTANCE », derived from the access
chain: *discoverable by the scopes under its owner, in the same org, never
cross-org*, with an **explicit override** by the owner. This module is that
derivation, and nothing else.

**What it does NOT do, and that is half the batch.** It filters nothing, it gates
no call, it widens no list. A non-member keeps seeing *no configured key* — the
disclosure question (« there is an access to request, and from whom ») remains a
**product** matter, and R9 files it under an opt-in org setting, later. What is
delivered here is **descriptive**: the same list as before, where each instance now
says who sees it.

**Why the question has a derivable answer.** D2 removed the protection stake:
hiding protects nothing, everything is refused at call time. What remains is therefore a
matter of ergonomics, and it has an honest answer — *whoever can resolve it sees it*.
Resolution is already written once and for all in the walker
(`access.cascade.walk_cascade`); this module INVERTS it.

⚠️ **Inverting a walker risks writing a second copy of it** — the exact defect
that `keyStack.ts` already carries on the dashboard side, and which is known not to break:
it LIES. Two safeguards, both mechanical:

1. **No rule is copied.** The gates are read at their SOURCE — the registry
   (`providers.is_org_shareable`, `auth_modes`), the vault sharing
   (`share_mode`/`share_down`/`share_side`, same columns as
   `access.cascade._platform_instance_usable`), and the chain (`grants_chain`). A
   registry consulted twice is not duplication; a copied list
   would be.
2. **A test pits them against each other** (`tests/test_instance_visibility.py`): for each
   tier, the audience derived here and the real verdict of `walk_cascade` must
   agree on a real PostgreSQL. It is that test, not this comment, that prevents
   divergence.

**The scope vocabulary** is that of the edges of `grants` and of `share_down`:
`user:<sub>` · `group:<id>` · `org:<id>` · `platform` (everyone — the free tier).
There is no « nobody » scope: the absence of an audience is the empty list.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from .. import credentials_store, grants_chain, providers

logger = logging.getLogger(__name__)

# The « everyone » audience: a platform key on the free tier, which no allowlist
# closes. It is not a `grants` scope (it designates nobody there) — it is the
# word returned for an UNBOUNDED audience, and it needed one: returning the
# list of all subs would be wrong (it changes with every sign-up) and returning the empty
# list would be a misreading (empty = nobody).
EVERYONE = "platform"

# The three owner overrides (column `connector_instances.visibility`).
# ⚠️ **Nothing SETS them yet**: no surface writes this column, it is `inherited`
# everywhere. The other two branches are written and tested, not served —
# the action that sets them is a product batch (R9: « an org setting, opt-in »).
INHERITED, HIDDEN, ORG_WIDE = "inherited", "hidden", "org"


def _owner_scope(owner_type: str, owner_id: str) -> Optional[str]:
    """The OWNER's scope — the one who sees their instance no matter what.

    `member` carries `{org}:{sub}`: the owner is the PERSON, not the pair. A
    `user` (residue of the OAuth mounts, ADR 0033) is already a bare sub. `platform` has
    no id (house convention) and therefore has no nameable owner."""
    if owner_type == credentials_store.MEMBER:
        _, _, sub = (owner_id or "").partition(":")
        return f"user:{sub}" if sub else None
    if owner_type == "user":
        return f"user:{owner_id}" if owner_id else None
    if owner_type in ("org", "group", credentials_store.TENANT):
        # The tenant scope is that of the edges of `grants` (`tenant:<slug>`, L-keys).
        return f"{owner_type}:{owner_id}" if owner_id else None
    return None


def _owner_org(owner_type: str, owner_id: str) -> Optional[str]:
    """The owner's org, when it can be read WITHOUT a query.

    Serves only the `org` override (« let the whole org discover it »). A `group` does not
    carry its org in its id: a lookup would be needed, and today there is
    no row to override — we return None rather than open an N+1 for a case
    that nothing produces. The batch that SETS the override will do this lookup at write time, where
    it costs once."""
    if owner_type == credentials_store.MEMBER:
        org, _, _ = (owner_id or "").partition(":")
        return f"org:{org}" if org.isdigit() else None
    if owner_type == "org":
        return f"org:{owner_id}" if str(owner_id).isdigit() else None
    return None


def _platform_audience(connector: str, share_mode: str, share_down: Sequence,
                       label: str) -> list[str]:
    """Who resolves a PLATFORM key — mirror of `access.cascade._platform_instance_usable`
    and of `_platform_grant_meta`, read from the SAME columns, never from a copy.

    Three outcomes, in the order resolution takes them:

    - **the chain GRANTS** (0053, L5) — the audience IS the set of beneficiaries
      of the live edges;
    - **the chain REFUSES** — edges exist, all revoked: nobody anymore, and
      **no fallback** (this is what makes a revocation real);
    - **the chain is SILENT** (connector not switched over, or no edge ever targeted
      this key) — the old path, unchanged: `closed` ⟹ the allowlist and nothing
      else; `open` ⟹ the allowlist if it exists, otherwise **everyone** (the
      free tier).
    """
    con = providers.REGISTRY.get(connector)
    if not (con and "platform" in con.auth_modes):
        # The cascade's platform tier is gated on `auth_modes`: a byo-only connector
        # NEVER resolves a platform key. Announcing an audience for one would be
        # the lie that the B4 review had already flagged on the projection.
        return []
    down = [str(s) for s in (share_down or [])]
    if grants_chain.is_chained(connector):
        ref = grants_chain.instance_ref(label, connector)
        vivantes, existent = _chain_grantees(ref)
        if existent:
            return vivantes
        # SILENT → the old path, without one more branch.
    if share_mode == "closed":
        return down
    return down or [EVERYONE]


def _chain_grantees(resource_id: str) -> tuple[list[str], bool]:
    """(scopes of the LIVE edges, « do edges exist, revoked ones included »).

    The second term is what distinguishes REFUSES from SILENT, and it cannot be deduced from the
    first: « no live beneficiary » means *nobody anymore* if edges once
    existed, and *the old path* if none ever existed.

    Fail-open, logged, like the rest of this surface: `visible_to` is descriptive and
    consumed by nothing: taking down someone's key listing because the
    edges table did not answer would be out of proportion. We then return « no
    edge », which falls back to the legacy path — the widest audience, so never
    a false « nobody sees it »."""
    from ..db import grants as db_grants
    try:
        return (db_grants.live_grantees_for_resource(resource_id),
                bool(db_grants.resource_ids_with_edges([resource_id])))
    except Exception:
        logger.warning("instance visibility: edges unavailable (fail-open)",
                       exc_info=True)
        return ([], False)


def derive(owner_type: str, owner_id: str, connector: str, *, account: str = "",
           visibility: str = INHERITED, share_mode: str = "open",
           share_down: Sequence = (), share_side: Sequence = ()) -> list[str]:
    """The scopes that DISCOVER this instance. Sorted, deduplicated, never None.

    Pure in the sense that matters: everything that varies is an ARGUMENT, except the registry (a
    process constant) and — for a platform key of a switched-over connector — the
    edges. The rest (sharing, the override) is read by the caller, in bulk.

    ⚠️ `visible_to` answers « who DISCOVERS it », not « who uses it ». The two
    coincide by default (`inherited`) — that is the whole point of a derived
    visibility. A `hidden` override separates them on purpose: whoever resolves
    keeps resolving, they only stop seeing it listed as a shareable
    object. Hiding protects nothing (D2), so it is not a security
    notch: it is an ERGONOMICS notch, and it is stated here so that nobody ever mistakes it
    for the other."""
    proprietaire = _owner_scope(owner_type, owner_id)
    if visibility == HIDDEN:
        return [proprietaire] if proprietaire else []

    if owner_type == credentials_store.PLATFORM:
        audience = _platform_audience(connector, share_mode, share_down,
                                      label=str(owner_id))
    elif owner_type in ("org", "group", credentials_store.TENANT):
        # SHARED tiers (team, org, tenant — L-keys PR 1) are only traversed
        # by the walker for an org-shareable connector: a team key on
        # a per-person connector exists in the vault and is read by nobody.
        # Announcing it as visible would be wrong.
        audience = [proprietaire] if (proprietaire and
                                      providers.is_org_shareable(connector)) else []
    else:
        # Member (and the `user` residue): someone's key is seen only by them.
        # Never cross-org — the cross-org instance of #172 is MINE seen from elsewhere,
        # so the same `user:` scope, not one more scope.
        audience = [proprietaire] if proprietaire else []

    # Named loans (ADR 0044 `share_side`) are an EXTENSION: they are always
    # added, at every tier, and may target outside the owner's org —
    # that is an explicit act by the owner, not a discovery.
    audience += [str(s) for s in (share_side or [])]

    if visibility == ORG_WIDE:
        org = _owner_org(owner_type, owner_id)
        if org:
            audience.append(org)
    return sorted(set(a for a in audience if a))
