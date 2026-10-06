"""The PLATFORM rung of the cascade (ADR 0044 §F, blueprint ADR 0053 lot L5).

Extracted from `cascade.py` (17/09, `status_for` N+1 batch): this seam — which, from a
platform instance, designates the beneficiary, computes its quota, and arbitrates
between the grants chain and the old path — depends only on `scope`
(membership in a sharing scope) and on `grants_chain`. The walker
(`walk_cascade`) calls it via the `platform` rung of a `CascadeProbe`,
never directly.
"""
from __future__ import annotations

from .. import credentials_store, grants_chain
from . import scope


def _platform_grantee_scope(sub, active_org, scopes) -> "str | None":
    """The scope in `scopes` that targets `sub` on a PLATFORM instance, or None (ADR 0044
    §F). `user:<sub>` wins (the most specific); `org:<id>` gated on the **ACTIVE** org
    (EXACT mirror of the old `get_active_org_grant(active_org)` — an org grant is metered
    per org context, not per membership: a member of org X active in Y does not benefit
    from it). Serves access (closed) AND quota (rate_limit_by)."""
    if not scopes:
        return None
    if f"user:{sub}" in scopes:
        return f"user:{sub}"
    if active_org is not None and f"org:{active_org}" in scopes:
        return f"org:{active_org}"
    return None


def _platform_instance_usable(sub, active_org, inst: dict) -> bool:
    """Is the platform instance usable by `sub`? (ADR 0044 §F, mode-aware). A `share_side`
    loan authorizes (membership, like a BYO loan). Otherwise per `share_mode`:
    'open' = empty `share_down` (free tier, open to all) OR `sub` is a grantee; 'closed' =
    `sub` is a grantee (closed by default)."""
    down, side = inst.get("share_down") or [], inst.get("share_side") or []
    if scope._sub_matches_scopes(sub, side):
        return True
    granted = _platform_grantee_scope(sub, active_org, down) is not None
    if inst.get("share_mode") == "closed":
        return bool(down) and granted
    return (not down) or granted


def _platform_quota(sub, active_org, meta: dict) -> "int | None":
    """Daily quota of the beneficiary on a platform instance: `rate_limit_by[sub's scope]`
    (user wins > active org), otherwise the instance's default `rate_limit`."""
    rlb = (meta or {}).get("rate_limit_by") or {}
    sc = _platform_grantee_scope(sub, active_org, list(rlb.keys()))
    if sc is not None and sc in rlb:
        return rlb[sc]
    return (meta or {}).get("rate_limit")


def _legacy_platform_grant_meta(sub, provider, active_org, *,
                                instances: "list[dict] | None" = None) -> "dict | None":
    """Platform rung (ADR 0044 §F R3) WITHOUT a secret: {label, daily_quota} of the most
    recent PLATFORM instance usable by `sub`, or None. Basis of the `status_for`/
    `credential_mode_for` mirrors (presence + quota, never decryption).

    ⚠️ **The old path, and it does not move by a single byte** (blueprint ADR 0053, lot L5):
    it remains the only one for the nine non-switched connectors, and the EXACT fallback for a
    beneficiary the chain does not know. The `_legacy_` prefix does not deprecate it —
    it names one of the two paths of the double-read window.

    `instances`: read in advance (cf. `cascade.preloaded_presence_probe`) — `None` rereads."""
    for inst in (instances if instances is not None
                 else credentials_store.list_platform_instances(provider)):
        if _platform_instance_usable(sub, active_org, inst):
            return {"label": inst["label"],
                    "daily_quota": _platform_quota(sub, active_org, inst.get("meta"))}
    return None


def _platform_grant_meta(sub, provider, active_org, *,
                         instances: "list[dict] | None" = None) -> "dict | None":
    """The platform rung, **grants chain first** (blueprint ADR 0053, lot L5).

    Three outcomes, and the third is what makes the window safe:

    - the chain GRANTS → its verdict (key + quota carried by the edge);
    - the chain REFUSES (edges exist, all revoked) → refusal **without fallback**:
      otherwise revoking an edge would cut nothing, the old free-tier path
      immediately re-granting;
    - the chain is SILENT (connector not switched, or no edge ever targeted this
      caller) → the old path, unchanged.

    Both paths are read for a switched connector — this is the accepted price of the
    window (one more indexed read) and it is what produces the discrepancy journal,
    the material for the end-of-window verdict.

    `instances`: passed as is to BOTH paths — two computations on one read."""
    verdict = grants_chain.platform_rung(sub, provider, active_org, instances=instances)
    if verdict is None:
        return _legacy_platform_grant_meta(sub, provider, active_org, instances=instances)
    legacy = _legacy_platform_grant_meta(sub, provider, active_org, instances=instances)
    grants_chain.journal_resolution(provider, sub, active_org, verdict, legacy)
    if not verdict.granted:
        return None
    return {"label": verdict.label, "daily_quota": verdict.quota}


def _resolve_platform_grant(sub, provider, active_org) -> "dict | None":
    """Platform rung WITH a secret: {label, secret, daily_quota} or None. Replaces the 3
    legacy reads (get_active_grant/get_active_org_grant/get_platform_api_key). The secret
    is decrypted ONLY for the winning instance (hot path)."""
    g = _platform_grant_meta(sub, provider, active_org)
    if not g:
        return None
    secret = credentials_store.get_credential(credentials_store.PLATFORM, g["label"], provider)
    if secret is None:
        return None
    return {**g, "secret": secret}
