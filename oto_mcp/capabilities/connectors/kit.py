"""Capabilities of the org KIT — the three org gestures (ADR 0050 §E8, oto#166).

"Set the whole kit" (`connectors.recommend`), "add to the kit"
(`connectors.bulk_select`, formerly "activate for the whole org") and "remove from the
kit" (`connectors.unset_default`) all go through ONE function, `connectors.kit.appliquer`,
which writes the kit and the members' toolboxes in the same transaction and returns a
per-connector quantified response.

The `Capability(...)` declarations stay in `selection.py`, where they belong: the route
order is frozen (`tests/api/api_routes_table.txt`). This module carries the
input/output shapes and the handlers.

The fields returned BEFORE this lot (`recommended`, `activated`, `skipped`,
`added_to_org_defaults`, `removed`) are still served, same meaning: two fronts read them
(the dashboard, and the partner tenant's). The kit fields are ADDED to them.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ...connectors import kit as connector_kit
from .._types import AuthzDenied, ResolvedCtx


class RecommendInput(BaseModel):
    org_id: int                          # injected from the {id} placeholder (ORG_ADMIN_OF)
    connectors: list[str] = []           # the WHOLE kit; [] = empty kit


class BulkSelectInput(BaseModel):
    org_id: int
    name: str                            # connector (placeholder {name}, auto-mapped)


class UnsetDefaultInput(BaseModel):
    org_id: int
    name: str                            # connector (placeholder {name}, auto-mapped)


class KitChange(BaseModel):
    """What a connector ADDED to the kit or REMOVED from the kit did to the members.

    An addition (`change="added"`) carries the five install counters; a removal
    (`change="removed"`) carries `uninstalled` and `kept`. An absent field is not
    zero: it does not belong to that kind of change."""
    connector: str
    change: Literal["added", "removed"]
    installed: Optional[int] = None          # set for N members (provenance `kit`)
    already_active: Optional[int] = None     # already installed and active: untouched
    paused: Optional[int] = None             # paused by the member: untouched
    removed_by_member: Optional[int] = None  # removed by the member themselves: left removed
    # Push only: the date on which the member removed it themselves.
    removed_at: Optional[str] = None
    uninstalled: Optional[int] = None        # removal: uninstalled for D members
    # Removal: members who KEEP it, by provenance of their installation — `membre`,
    # `admin`, `socle`, `inconnue` (decision Q1: only what the kit set goes away).
    kept: Optional[dict[str, int]] = None


class _KitApplied(BaseModel):
    """The kit after the gesture, and what the gesture did to the members."""
    kit: list[str]                           # the org's kit AFTER the gesture
    members: int                             # org members the gesture applied to
    changes: list[KitChange]                 # one per added or removed connector
    # Connectors named by the gesture that did NOT change the kit (already in it
    # for an addition, absent for a removal): nothing is replayed for the members.
    unchanged: list[str]
    # Kit connectors that the org cut AFTER putting them there: they stay in it,
    # installed and hidden for everyone, and come back on their own when reopened (§E2).
    cut: list[str] = []
    note: str                                # when a member's agent will see it
    unchanged_note: Optional[str] = None     # why nothing was replayed, and how to proceed
    cut_note: Optional[str] = None           # present when `cut` is not empty


class OrgRecommendedConnectors(_KitApplied):
    """Echo of the whole kit as set. `recommended` = the kit (name from before the kit,
    kept for the fronts that read it)."""
    org_id: int
    recommended: list[str]


class BulkSelectResult(_KitApplied):
    """"Add to the kit" (formerly "activate for the whole org")."""
    org_id: int
    connector: str
    activated: int                   # members for whom the connector was just installed
    # Members left as they were: already active, paused, or removed by themselves —
    # the detail is in `changes`. `skipped>0` is not an anomaly.
    skipped: int
    # `false` = the connector was ALREADY in the kit: nothing is replayed (see `unchanged_note`).
    added_to_org_defaults: bool


class UnsetDefaultResult(_KitApplied):
    """"Remove from the kit". `removed=false` = it wasn't there."""
    org_id: int
    connector: str
    removed: bool


def appliquer_servi(org_id: int, **geste) -> dict:
    """`connectors.kit.appliquer`, its refusals translated for the two faces. Shared by
    the kit gestures and by the push (`force.py`)."""
    try:
        return connector_kit.appliquer(org_id, **geste)
    except connector_kit.OrgInconnue:
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} unknown.")
    except connector_kit.AjoutRefuse as e:
        _refus_d_ajout(e)


def _refus_d_ajout(e) -> None:
    """§E2 — the refusal says WHY, at the gesture: the admin learns it here, not weeks
    later at a member's. Same tokens as those already served by the activation
    governance. Three LITERAL `raise`s and not a computed pair: the ratchet of declared
    refusals (`tests/_refus_atteignables.py`) only reads literals — a computed code
    makes the declaration "decorative" in its eyes, even though it is served."""
    raisons = {r["reason"] for r in e.refus}
    detail = " ; ".join(f"`{r['connector']}` {connector_kit.RAISONS[r['reason']]}"
                        for r in e.refus)
    message = f"Refused, nothing was written (neither to the kit nor to your members): {detail}."
    details = {"refused": e.refus}
    if "unknown" in raisons:
        raise AuthzDenied(404, "unknown_connector", message, details=details)
    if "platform_disabled" in raisons:
        raise AuthzDenied(409, "platform_disabled", message, details=details)
    raise AuthzDenied(409, "org_disabled", message, details=details)


def _change(out: dict, name: str) -> Optional[dict]:
    return next((c for c in out["changes"] if c["connector"] == name), None)


def _recommend(ctx: ResolvedCtx, inp: RecommendInput) -> dict:
    """[org admin] Sets the WHOLE kit; only its difference with the current kit
    applies to the members (see `connectors.kit`)."""
    out = appliquer_servi(inp.org_id, kit=inp.connectors)
    return {"org_id": inp.org_id, "recommended": out["kit"], **out}


def _bulk_select(ctx: ResolvedCtx, inp: BulkSelectInput) -> dict:
    """[org admin] Adds `name` to the kit: installed for each current member who
    doesn't have it (never over their choice), and for any future member at seeding."""
    # The write guard (§E2) is INSIDE the apply function: same refusal,
    # same tokens (`unknown_connector`, `org_disabled`, `platform_disabled`) for the
    # three gestures. A connector already in the kit is not an addition: it is not judged.
    out = appliquer_servi(inp.org_id, ajouter=[inp.name])
    ch = _change(out, inp.name)
    laisses = (ch["already_active"] + ch["paused"] + ch["removed_by_member"]) if ch else 0
    return {"org_id": inp.org_id, "connector": inp.name,
            "activated": ch["installed"] if ch else 0, "skipped": laisses,
            "added_to_org_defaults": ch is not None, **out}


def _unset_default(ctx: ResolvedCtx, inp: UnsetDefaultInput) -> dict:
    """[org admin] Removes `name` from the kit. Never hides the library's connector
    (it is not the exposure lever)."""
    out = appliquer_servi(inp.org_id, retirer=[inp.name])
    return {"org_id": inp.org_id, "connector": inp.name,
            "removed": _change(out, inp.name) is not None, **out}
