"""The THIN views on resolution — one contract per use.

A keyed tool wants a key (`resolve_api_key`), a multi-secret client wants its
fields (`resolve_credential_fields`), the dashboard wants to know UNDER WHICH ORIGIN
it would resolve without decrypting anything (`credential_mode_for`), and the
published endpoint wants to know whether an org can resolve on its own
(`connector_resolvable_for_org`).

⚠️ `resolve_mount_token` lived here until 2026-09-09: it was the view of the
per-user OAuth token of a federated MCP, and it left with the mechanism (ADR 0069).

All of them derive from `resolve` or from the walker with a presence probe: none
copies the cascade — a divergence would make a surface LIE (seen 2026-07-07:
the option rule copied three times, diverged). `option_open` is here, and not in
`quotas`, because it crosses the entitlement with BYO — hence with the credential
mode, hence with the cascade.
"""
from __future__ import annotations

from typing import Optional

from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import providers, credentials_store, db, org_store
from . import cascade, chain_shadow, quotas, resolve, scope


# The TENANT key is the tenant's BYO (L-keys PR 1): it manages its own instance
# at the provider, no platform seat to protect — the layer-3 option is raised
# by construction, as for an org key.
BYO_MODES = ("user", "group", "org", "tenant")


# (resolve_remote_credential removed — ADR 0034 B4: the universal `bridge`
# connector resolves through the standard fields, see resolve_credential_fields.)


def option_open(sub: str, connector: str, *, org: "int | None | object" = scope._UNSET,
                group: "int | None | object" = scope._UNSET) -> bool:
    """SINGLE SOURCE of "is the connector's option (layer 3) raised for `sub`?".
    The card status (`connectors_selection.option_ok`) AND unipile's "connect" gate
    (`status_for.subscribed`) call it → they can no longer DIVERGE (BYO opened
    the option here but not there → inconsistent "org key" + "Blocked" card, fixed
    2026-07-07). Rule: no option required ⟹ open; otherwise **BYO** (own
    user/group/org key — the user manages their own instance) OR **has_option**
    (declared entitlement of the org or of the person `sub`). Explicit `org`/`group` =
    computation for a third party (admin page)."""
    opt = quotas.paid_option_for(connector)
    if opt is None:
        return True
    if credential_mode_for(sub, connector, org=org, group=group) in BYO_MODES:
        return True
    return quotas.has_option(sub, opt, org=org)


def resolve_api_key(provider: str, account: Optional[str] = None,
                    units: int = 1) -> tuple[str, bool]:
    """Returns `(api_key, is_platform)` or raises an actionable McpError. Thin view
    on `resolve_credential` (contract unchanged for the ~15 keyed tools; optional
    `account` selects the account in multi-account). `units`: size of a batch the
    call will debit, so that the shared key's quota is checked for the whole
    batch (default 1 = single call)."""
    rc = resolve.resolve_credential(provider, want="auto", account=account, units=units)
    return rc.key, rc.is_platform


def resolve_credential_fields(provider: str, account: Optional[str] = None) -> dict:
    """Resolves a **multi-field** byo_user credential (generic model, ADR 0011)
    of the current sub → dict of the declared fields (`Connector.secret_fields`).

    For in-process connectors whose client is instantiated with several
    secrets (e.g. Silae: client_id / client_secret / subscription_key, OAuth2
    client-credentials). **byo-only**: no platform key and no quota — the
    credential IS the grant. Thin view on `resolve_credential`
    (user > group > org cascade, without the platform tier; `account` selects
    the account in multi-account)."""
    return resolve.resolve_credential(provider, want="byo", account=account).fields


def credential_mode_for(sub: str, provider: str, *,
                        org: "int | None | object" = scope._UNSET,
                        group: "int | None | object" = scope._UNSET,
                        probe: "Optional[CascadeProbe]" = None) -> str:
    """Origin of the `provider` key for `sub` (EXPLICIT, outside MCP context):
    `user|group|org|tenant|platform|over_quota|forbidden`. PRESENCE only (no
    decryption → safe/light for a status). **Mirror** of the `resolve_credential`
    cascade (incl. org grant fallback) — a divergence would make the UI lie.
    "BYO" (own key, not the platform) = mode ∈ {user, group, org}.
    Explicit `org`/`group` (≠ _UNSET) = computation for a THIRD PARTY against their own
    context (admin page), without current_org/current_group (no leak of the requester).

    `probe` = ALTERNATIVE presence probe (default: `PRESENCE_PROBE`). A caller that
    queries MANY connectors in a row passes a **preloaded** probe
    (`preloaded_presence_probe`): same answers, in a few reads instead of one
    walk per connector. The parameter exists so that this case goes THROUGH this
    function — the quota check of the platform rung, just below, is not copied
    into the caller, and a hurried caller has no reason to bypass the seam.

    ⚠️ Passing `org`/`group` explicitly is NOT only a shortcut for a third party: it is
    also what avoids re-resolving the context on EVERY call. Measured on 33
    connectors of a real account — `current_org` accounted for 73% of total time, called
    thirty-three times to return the same value thirty-three times."""
    o = scope.current_org(sub) if org is scope._UNSET else org
    g = scope.current_group(sub) if group is scope._UNSET else group
    # Single walk (walker) with a PRESENCE probe — no more cascade copied here:
    # the mirror is structural, it can no longer diverge from the resolution.
    win = next(chain_shadow.resolution_rungs(sub, provider, org=o, group=g,
                 probe=probe or cascade.PRESENCE_PROBE, want="auto"), None)
    if win is None:
        return "forbidden"
    if win.mode != "platform":
        return win.mode
    # The same (counter, ceiling) pair as the refusal — including the person's
    # `platform_unmetered` lift: otherwise the UI would announce "quota exhausted" to someone being served.
    used, limit = resolve._win_quota(win, sub, provider, o)
    return "over_quota" if (limit and used >= limit) else "platform"


def credential_rejection_for(sub: str, provider: str, *,
                             org: "int | None | object" = scope._UNSET,
                             group: "int | None | object" = scope._UNSET,
                             probe: "Optional[CascadeProbe]" = None) -> Optional[str]:
    """The REJECTION recorded on the key that would resolve for `sub` — or `None`.

    Same walk as `credential_mode_for`, same walker: we read the health of the row
    the call would REALLY use, not of a neighbouring row. Without that, a healthy
    personal key would mask the rejection of an org key — or the reverse.

    ⚠️ **This is a SECOND cascade walk** (~22 ms on a real account, see
    `connectors/readiness`). Accepted rather than making `credential_mode_for`
    return two things: the platform rung's quota check lives there, and
    copying it here would reopen the divergence the single walker closed. It is also
    why this computation is only done on a TARGETED read of one connector.

    The verdict itself is written by the `oto_instance op=verify` probe; it is lifted by
    replaying it (it writes `health_ko: false` on success) or by re-setting the key
    (a re-set rewrites `meta`)."""
    o = scope.current_org(sub) if org is scope._UNSET else org
    g = scope.current_group(sub) if group is scope._UNSET else group
    win = next(chain_shadow.resolution_rungs(sub, provider, org=o, group=g,
                 probe=probe or cascade.PRESENCE_PROBE, want="auto"), None)
    if win is None or win.entity_type is None:
        return None
    return credentials_store.credential_health(
        win.entity_type, win.entity_id, providers.credential_provider(provider),
        win.account or "")


def connector_resolvable_for_org(provider: str, org_id: int) -> bool:
    """Can a connector be resolved for an ORG **without an identified user**?
    True if: credential-less (`secret_kind='none'`), OR org secret configured, OR
    platform key granted to the org. Probe for publishing an **anonymous** MCP
    endpoint (ADR 0032) served by the key of the org that owns the project: an
    endpoint without login has no `user_key`/per-user session → oauth/cookie are
    excluded de facto (no org secret for them). Org-only mirror of the `resolve_credential` cascade."""
    con = providers.connector_for_provider(provider)
    if con is None:
        return False
    if con.secret_kind == "none":
        return True
    # Walker with presence, sub=None → reduced org > platform cascade (ADR 0044
    # §F R3: 'open' free-tier instance, or 'closed' targeting `org:<org_id>`).
    return cascade.cascade_winner(None, provider, org=org_id, group=None,
                          probe=cascade.PRESENCE_PROBE) is not None
