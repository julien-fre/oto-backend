"""Capabilities "connector activation at org level" — HARD org ceiling (ADR 0022).

ADR 0019 distinguishes exposure (platform ceiling), proposal (recommendation) and
selection (member). This module opens the **third governance notch for the org_admin**:
the **per-org activation override** (`connector_activation`, already in the DB). The
org_admin can, for THEIR own org, force a connector OFF (remove it for all their
members) or ON.

**Guardrail (ADR 0022 §4)**: an org override cannot *expose* what the platform has
cut — `enabled=True` is only accepted if the global master exposes the connector. The
platform deny-by-default is never loosened by an org; the org can always restrict.

Read = `ORG_MEMBER_OF` (members see the governance), write = `ORG_ADMIN_OF`.
`refresh_visibility=True`: the toggle re-pushes visibility onto the caller's MCP session.
Effect for the other members: at their next session (gate at visibility, not at boot).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ... import access, group_store, org_store, providers
from ...connectors import activation as connector_activation
from .._authz import GROUP_ADMIN_OF, GROUP_MEMBER_OF, ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}      # placeholder {id} → Input field org_id
_GID = {"id": "group_id"}   # placeholder {id} → Input field group_id

# Layer 3 (connector option, ADR 0024): connector → unlockable option.
# Today only unipile (the "hosted messaging" option). Curated map — no generic
# field in the registry as long as there is only one option.
# Paid option per connector → canonical home in access.paid_option_for (derive don't duplicate).


def _org_subscribed(org_id: int, option: str) -> bool:
    """Does the org have the `option` option unlocked? Best-effort (never makes
    the list read fail).

    ⚠️ **Fixed on 2026-09-02**: it read `db.has_option_comp('org', …)` directly,
    so it ONLY saw the admin grant — an org that PAID showed as "not
    subscribed". It now reads the org's declared entitlement (`access.org_has`, ADR 0070
    §7), the same rule as every other path. Org grain INTENDED: the cockpit
    describes the space, not the personal grant of whoever opens it."""
    try:
        return access.org_has(org_id, option)
    # noqa: SILENT — unreadable paid option ⇒ not subscribed (fail-closed for paid)
    except Exception:
        return False


class OrgActivationListInput(BaseModel):
    org_id: int


class OrgActivationSetInput(BaseModel):
    org_id: int
    name: str                  # connector (placeholder {name}, auto-mapped)
    enabled: bool


class OrgActivationClearInput(BaseModel):
    org_id: int
    name: str


class ActivationOverrideSet(BaseModel):
    """Echo of a SET of an activation override. Exactly ONE of the two scope ids is
    present, depending on the route taken (org or team) — it is the same
    gesture at two grains, not two objects.

    ⚠️ At TEAM grain, `enabled` is ALWAYS `false`: the invariant is monotonic
    (a team can only cut), `enabled=true` is refused with a 409. To
    reopen, REMOVE the cut (`clear`), don't set it to `true`."""
    org_id: Optional[int] = None            # present at ORG grain
    group_id: Optional[int] = None          # present at TEAM grain
    connector: str
    enabled: bool
    # ORG grain only, and present only if the connector is in the org's KIT
    # (ADR 0050 §E2): cutting does not remove it from the kit — it stays there, installed
    # and hidden for everyone, and comes back on its own when reopened. `kit_note` says so.
    in_kit: Optional[bool] = None
    kit_note: Optional[str] = None


class ActivationOverrideCleared(BaseModel):
    """Echo of a REMOVAL of an override — the connector falls back to the notch above
    (platform master for an org, org exposure for a team).

    ⚠️ `cleared` is ALWAYS `true`: the operation is idempotent and does not count
    the rows deleted. It therefore does NOT prove an override existed."""
    org_id: Optional[int] = None            # present at ORG grain
    group_id: Optional[int] = None          # present at TEAM grain
    connector: str
    cleared: bool
    in_kit: Optional[bool] = None           # see `ActivationOverrideSet`
    kit_note: Optional[str] = None


class OrgActivationRow(BaseModel):
    """A connector in the org's governance cockpit: BOTH notches
    (platform ceiling, org override) and their result."""
    connector: str
    label: str
    help: Optional[str] = None
    namespaces: list[str]
    # `None` = no platform activation row has EVER been set, which
    # means OFF. Distinct from `false` (explicitly cut) — both read
    # "not exposed", only the second is a decision.
    master_enabled: Optional[bool] = None
    org_enabled: Optional[bool] = None      # None = no override, the org follows the master
    # The CEILING of the tenant that hosts the org (2026-09-26): `false` = cut by the
    # host for all its orgs, and the org cannot reopen it; `None` =
    # no row (or primary tenant's org).
    tenant_enabled: Optional[bool] = None
    effective: bool                         # (override > master > OFF) under the tenant ceiling
    recommended: bool                       # org baseline (ADR 0019), not the activation
    paid_option: Optional[str] = None       # paid add-on required (layer 3), None = none
    # `false` by default AND when no option is required — so it does NOT
    # read as "the org hasn't paid" outside the `paid_option != null` case.
    subscribed: bool


class OrgActivation(BaseModel):
    """The org's activation cockpit. The list is FILTERED at the platform ceiling:
    a connector that the platform never exposed and that the org did not
    override does not appear (no inert lever) — the absence of a row is
    therefore not proof that the connector doesn't exist."""
    org_id: int
    connectors: list[OrgActivationRow]


class GroupActivationRow(BaseModel):
    """A connector in the team cockpit. The team has only one lever —
    cutting — hence only two booleans: what the org makes available, and what
    the team cut."""
    connector: str
    label: str
    help: Optional[str] = None
    namespaces: list[str]
    org_available: bool
    group_cut: bool
    effective: bool                         # org_available AND NOT group_cut


class GroupActivation(BaseModel):
    """The team's activation cockpit. List = what the org exposes, PLUS the
    residual cuts of a team on a connector the org no longer exposes
    (otherwise the cut would become invisible and irremediable)."""
    group_id: int
    connectors: list[GroupActivationRow]


def _org_list(ctx: ResolvedCtx, inp: OrgActivationListInput) -> dict:
    """For each connector in the registry: global master, THIS org's override,
    and the effective state (override > master > OFF). Plus `recommended` (org baseline)."""
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} unknown.")
    glob: dict[str, bool] = {}
    override: dict[str, bool] = {}
    for r in connector_activation.list_activations():
        if r["org_id"] is None:
            glob[r["connector"]] = bool(r["enabled"])
        elif r["org_id"] == inp.org_id:
            override[r["connector"]] = bool(r["enabled"])
    recommended = set(org_store.get_org_default_connectors(inp.org_id) or [])
    slug = connector_activation.tenant_of_org(inp.org_id)
    tenant_map = connector_activation.list_tenant_activations(slug) if slug else {}
    out = []
    for name, c in providers.REGISTRY.items():
        master = glob.get(name)          # None = never set = OFF
        org_ov = override.get(name)      # None = no override
        tenant_ov = tenant_map.get(name)  # None = no tenant row
        # Invariant: only list what the platform makes AVAILABLE to this org —
        # consistent with the USER surface (_visible_catalog). We filter on the platform
        # CAP (master), not on `effective` (otherwise a connector the org
        # overrode OFF would disappear → impossible to re-enable). master OFF + no
        # override = never activated → invisible (no more inert lever).
        if not master and org_ov is None:
            continue
        effective = org_ov if org_ov is not None else bool(master)
        if tenant_ov is False:
            effective = False           # host ceiling: nothing below reopens it
        option = access.paid_option_for(name)          # paid add-on (layer 3) or None
        out.append({
            "connector": name, "label": c.label, "help": c.help,
            "namespaces": list(c.namespaces),
            "master_enabled": master, "org_enabled": org_ov, "tenant_enabled": tenant_ov,
            "effective": effective,
            "recommended": name in recommended,
            "paid_option": option,
            "subscribed": _org_subscribed(inp.org_id, option) if option else False,
        })
    return {"org_id": inp.org_id, "connectors": out}


def _require_master_exposed(name: str) -> None:
    """The global master must expose the connector for an org to be able to enable it."""
    if not connector_activation.is_exposed(name, org_id=None):
        raise AuthzDenied(409, "platform_disabled",
                          f"Connector `{name}` is disabled by the platform — your org cannot "
                          f"enable it (the platform ceiling is never loosened).")


_KIT_COUPE = ("It is in your organization's kit: cutting does not remove it from there. It stays "
              "installed for your members, hidden for everyone while it is cut, and comes back "
              "on its own when reopened. To stop it from being installed, remove it from the kit.")
_KIT_OUVERT = ("It is in your organization's kit: its tools come back for the "
               "members who installed it, at their next conversation.")


def _signal_kit(org_id: int, name: str) -> dict:
    """ADR 0050 §E2 — the response to the cut (or the reopening) says that the
    connector is in the kit. Read AFTER the gesture: it is the resulting state that is described.
    The gesture has already succeeded when we read: a read failure SAYS so instead of
    turning a success into an error."""
    try:
        if name not in (org_store.get_org_default_connectors(org_id) or []):
            return {}
        ouvert = name in connector_activation.exposed_connectors(org_id)
    # noqa: SILENT — the unread kit state is SAID in the response, never a 500 on a success
    except Exception:
        return {"kit_note": "Kit membership could not be read: nothing is said here "
                            "about the kit, one way or the other."}
    return {"in_kit": True, "kit_note": _KIT_OUVERT if ouvert else _KIT_COUPE}


def _require_tenant_exposed(org_id: int, name: str) -> None:
    """The tenant that hosts the org must not have cut it: its ceiling is no more
    loosened than a platform ceiling (2026-09-26)."""
    slug = connector_activation.tenant_of_org(org_id)
    if slug and connector_activation.list_tenant_activations(slug).get(name) is False:
        raise AuthzDenied(409, "tenant_disabled",
                          f"Connector `{name}` cut by your organization's host — "
                          "an org does not reopen it; a tenant admin does so from their "
                          "dashboard.")


def _org_set(ctx: ResolvedCtx, inp: OrgActivationSetInput) -> dict:
    if inp.name not in providers.REGISTRY:
        raise AuthzDenied(404, "unknown_connector", f"Connector `{inp.name}` unknown.")
    if inp.enabled:
        _require_master_exposed(inp.name)
        _require_tenant_exposed(inp.org_id, inp.name)
    connector_activation.set_activation(inp.name, inp.enabled, org_id=inp.org_id, set_by=ctx.sub)
    return {"org_id": inp.org_id, "connector": inp.name, "enabled": inp.enabled,
            **_signal_kit(inp.org_id, inp.name)}


def _org_clear(ctx: ResolvedCtx, inp: OrgActivationClearInput) -> dict:
    """Deletes the org override → the connector falls back to the global master."""
    if inp.name not in providers.REGISTRY:
        raise AuthzDenied(404, "unknown_connector", f"Connector `{inp.name}` unknown.")
    connector_activation.clear_activation(inp.name, inp.org_id)
    return {"org_id": inp.org_id, "connector": inp.name, "cleared": True,
            **_signal_kit(inp.org_id, inp.name)}


# ── TEAM tier (ADR 0012, restrict-only) ──────────────────────────────────────
# A team lead (`GROUP_ADMIN_OF`) can CUT a connector for THEIR team —
# never expose it beyond what the org allows (MONOTONIC invariant). The team
# therefore has only one "cut / reopen" lever; the floor stays org > platform.

class GroupActivationListInput(BaseModel):
    group_id: int


class GroupActivationSetInput(BaseModel):
    group_id: int
    name: str
    enabled: bool


class GroupActivationClearInput(BaseModel):
    group_id: int
    name: str


def _group_org_id(group_id: int) -> int:
    g = group_store.get_group(group_id)
    if not g:
        raise AuthzDenied(404, "unknown_group", f"Team #{group_id} unknown.")
    return g["org_id"]


def _group_list(ctx: ResolvedCtx, inp: GroupActivationListInput) -> dict:
    """For each connector exposed to the team's org: the effective state for
    the team (org exposes AND not cut) + whether the team cut it. We only list what
    the org makes available (plus a possible residual cut) — no inert
    lever, consistent with the org surface."""
    org_id = _group_org_id(inp.group_id)
    exposed = connector_activation.exposed_connectors(org_id)
    cut = connector_activation.group_cut_connectors(inp.group_id)
    out = []
    for name, c in providers.REGISTRY.items():
        org_available = name in exposed
        group_cut = name in cut
        if not org_available and not group_cut:
            continue
        out.append({
            "connector": name, "label": c.label, "help": c.help,
            "namespaces": list(c.namespaces),
            "org_available": org_available,
            "group_cut": group_cut,
            "effective": org_available and not group_cut,
        })
    return {"group_id": inp.group_id, "connectors": out}


def _require_org_available(group_id: int, name: str) -> None:
    """The org must expose the connector for a team to be able to cut it (otherwise
    it is already off — nothing to restrict). Mirror of `_require_master_exposed`."""
    org_id = _group_org_id(group_id)
    if name not in connector_activation.exposed_connectors(org_id):
        raise AuthzDenied(409, "org_disabled",
                          f"Connector `{name}` not available in the org — nothing to cut for the team.")


def _group_set(ctx: ResolvedCtx, inp: GroupActivationSetInput) -> dict:
    if inp.name not in providers.REGISTRY:
        raise AuthzDenied(404, "unknown_connector", f"Connector `{inp.name}` unknown.")
    # MONOTONIC invariant: a team can only RESTRICT. `enabled=True` (exposing
    # beyond the org) is refused — to reopen, REMOVE the cut (clear).
    if inp.enabled:
        raise AuthzDenied(409, "group_cannot_expose",
                          "A team can only restrict (cut) a connector, never "
                          "expose it beyond the org. To reopen it, remove the cut.")
    _require_org_available(inp.group_id, inp.name)
    connector_activation.set_group_activation(inp.group_id, inp.name, False, set_by=ctx.sub)
    return {"group_id": inp.group_id, "connector": inp.name, "enabled": False}


def _group_clear(ctx: ResolvedCtx, inp: GroupActivationClearInput) -> dict:
    """Removes the team cut → the connector falls back to the org's exposure."""
    if inp.name not in providers.REGISTRY:
        raise AuthzDenied(404, "unknown_connector", f"Connector `{inp.name}` unknown.")
    connector_activation.clear_group_activation(inp.group_id, inp.name)
    return {"group_id": inp.group_id, "connector": inp.name, "cleared": True}


CAPABILITIES += [
    Capability(
        key="connectors.activation.group_list", handler=_group_list, Input=GroupActivationListInput,
        Output=GroupActivation,
        authz=GROUP_MEMBER_OF("group_id"),
        description="List, for a team, each connector available to its org: whether the org "
                    "exposes it, whether the team has cut it, and the effective state for the "
                    "team's members. The team cockpit of connector availability.",
        rest=RestBinding("GET", "/api/groups/{id}/connectors/activation", _GID),
    ),
    Capability(
        key="connectors.activation.set_group", handler=_group_set, Input=GroupActivationSetInput,
        Output=ActivationOverrideSet,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        description="[team lead] Cut a connector for your whole team (restrict-only — a team can "
                    "only narrow what the org allows, never expose beyond it). Requires the org to "
                    "expose it. Takes effect for members whose active team is this one, next session.",
        rest=RestBinding("PUT", "/api/groups/{id}/connectors/{name}/activation", _GID),
    ),
    Capability(
        key="connectors.activation.clear_group", handler=_group_clear, Input=GroupActivationClearInput,
        Output=ActivationOverrideCleared,
        authz=GROUP_ADMIN_OF("group_id"), refresh_visibility=True,
        description="[team lead] Remove your team's cut for a connector — it falls back to the "
                    "org's availability.",
        rest=RestBinding("DELETE", "/api/groups/{id}/connectors/{name}/activation", _GID),
    ),
    Capability(
        key="connectors.activation.org_list", handler=_org_list, Input=OrgActivationListInput,
        Output=OrgActivation,
        authz=ORG_MEMBER_OF("org_id"),
        description="List, for your org, each connector's activation: the platform master switch, "
                    "your org's override (if any), the effective state, and whether the org "
                    "recommends it. The org cockpit of connector governance.",
        rest=RestBinding("GET", "/api/orgs/{id}/connectors/activation", _ID),
    ),
    Capability(
        key="connectors.activation.set_org", handler=_org_set, Input=OrgActivationSetInput,
        Output=ActivationOverrideSet,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        description="[org admin] Force a connector ON or OFF for your whole org (hard ceiling). "
                    "Enabling requires the platform to expose it (the platform ceiling is never "
                    "lifted); disabling always works. Takes effect for members on their next session. "
                    "If the connector is in your org's kit, the response says so (`in_kit`, "
                    "`kit_note`): cutting it keeps it in the kit and installed, hidden for "
                    "everyone, and it comes back on its own when reopened.",
        rest=RestBinding("PUT", "/api/orgs/{id}/connectors/{name}/activation", _ID),
    ),
    Capability(
        key="connectors.activation.clear_org", handler=_org_clear, Input=OrgActivationClearInput,
        Output=ActivationOverrideCleared,
        authz=ORG_ADMIN_OF("org_id"), refresh_visibility=True,
        description="[org admin] Clear your org's activation override for a connector — it falls "
                    "back to the platform master switch.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/connectors/{name}/activation", _ID),
    ),
]
