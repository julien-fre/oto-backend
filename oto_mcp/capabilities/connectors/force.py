"""“Push to a member”: install a connector in ONE member's toolbox.

ADR 0050 §E, decision Q2 of 11/09/2026 (amends ADR 0031, force-connector-per-user).

The gesture used to set a per-tool visibility preference (`user_enabled_tools`), which the
selection regime ignores for a connector that isn't installed: in production, 18 pushes
from 11/08 to 11/09, 11 of which targeted a member who didn't have the connector — nothing
ever appeared for them, and the response said `ok`. The gesture now INSTALLS, provenance
`admin`, through the kit's single function (`connectors.kit.appliquer(…,
pousser_a=sub)`) — without touching the kit, and with the member's exceptions (§E6): an
active row is not rewritten; a PAUSE or a REMOVAL by the member is never undone, and the
gesture is then REFUSED (with the removal date) rather than answering `ok` for a gesture
that did nothing. No per-tool preference is written any more.

Installing is not authorizing (§E1): real access stays guarded at call time (credential).
authz `ORG_ADMIN_OF`: the org_admin governs THEIR org.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ... import db, org_store
from .._authz import ORG_ADMIN_OF
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from .kit import appliquer_servi
from .selection import _connector_tools

_ID_CONN = {"id": "org_id", "connector": "connector"}


class ForceConnectorInput(BaseModel):
    org_id: int
    connector: str
    member: str  # Logto sub OR email of the target member


class ForceConnectorResult(BaseModel):
    """The connector is installed and active in the member's toolbox, for this org —
    visible to their agent at their NEXT conversation. A gesture with no effect
    (pause or removal by the member) never gets here: it is refused."""
    ok: bool                                  # always true on a 200
    org_id: int
    connector: str
    # The member's RESOLVED sub — the input accepted an email, the response never returns it.
    member: str
    # `installed` = placed by this gesture (provenance `admin`); `already_active` = they
    # already had it, active (their row is not rewritten).
    result: Literal["installed", "already_active"]
    # Deprecated ALIAS (field from before decision Q2, which counted per-tool
    # preferences): the number of tools of the connector, read from the BOOT registry, that
    # their toolbox now carries. `0` = registry not warmed up (script outside the server),
    # not a connector without tools.
    tools_forced: int
    note: str


def _resolve_member(org_id: int, target: str) -> str:
    """Resolves the target member's sub (email accepted) + checks they belong to the org."""
    sub = target
    if "@" in target:
        u = db.get_user_by_email(target)
        if not u:
            raise AuthzDenied(404, "unknown_user", f"No user with the email `{target}`.")
        sub = u["sub"]
    if org_store.get_org_role(org_id, sub) is None:
        raise AuthzDenied(400, "user_not_in_org",
                          f"`{target}` is not a member of org #{org_id}.")
    return sub


async def _force_connector(ctx: ResolvedCtx, inp: ForceConnectorInput) -> dict:
    sub = _resolve_member(inp.org_id, inp.member)
    out = appliquer_servi(inp.org_id, ajouter=[inp.connector], pousser_a=sub)
    (ch,) = out["changes"]
    if ch["removed_by_member"]:
        raise AuthzDenied(
            409, "removed_by_member",
            f"Refused, nothing was written: this member removed `{inp.connector}` themselves on "
            f"{ch.get('removed_at')} (UTC). The platform does not undo their action; if they "
            f"need it, it is up to them to reinstall it.",
            details={"removed_at": ch.get("removed_at")})
    if ch["paused"]:
        raise AuthzDenied(
            409, "paused_by_member",
            f"Refused, nothing was written: this member has `{inp.connector}` installed and "
            f"paused it themselves. The platform does not resume it for them; they resume it "
            f"whenever they want.")
    return {"ok": True, "org_id": inp.org_id, "connector": inp.connector, "member": sub,
            "result": "installed" if ch["installed"] else "already_active",
            "tools_forced": len(_connector_tools(inp.connector)), "note": out["note"]}


CAPABILITIES += [
    Capability(
        key="connectors.force.member", handler=_force_connector, Input=ForceConnectorInput,
        authz=ORG_ADMIN_OF("org_id"), Output=ForceConnectorResult,
        description="[org admin] Install a connector in ONE member's toolbox in this org "
                    "(provenance `admin`) — it is NOT added to your org's kit. Never over "
                    "their own choice: if they paused it or removed it themselves, the push "
                    "is REFUSED and says so, with the date. Refused too if the connector is "
                    "unknown or not available for your org. Not an access grant: keys and "
                    "access rules still apply at call time. Their agent sees it at their "
                    "NEXT conversation. `member` = sub or email.",
        errors=(DeclaredError(404, "unknown_user", "no account carries this email"),
                DeclaredError(400, "user_not_in_org", "the target is not a member of the org"),
                DeclaredError(404, "unknown_connector", "name unknown to the registry"),
                DeclaredError(409, "org_disabled",
                              "connector not available to the org's members"),
                DeclaredError(409, "platform_disabled", "connector switched off by the platform"),
                DeclaredError(409, "removed_by_member",
                              "the member removed it themselves — never undone, nothing is written"),
                DeclaredError(409, "paused_by_member",
                              "the member paused it themselves — nothing is written"),),
        rest=RestBinding("POST", "/api/orgs/{id}/connectors/{connector}/force", _ID_CONN),
    ),
]
