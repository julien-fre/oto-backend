"""Seats of the unipile PLATFORM key: inventory them, release one (ADR 0009).

A seat is billed as long as it **exists at unipile** — disconnecting on the oto side does not
give it back (`clear_unipile_account` is a soft-disconnect: the row survives as proof
of ownership). There was therefore an inventory but no action: the cleanup went through
a script on the box, with the platform key in hand. That is what these two verbs
remove.

Three states, and above all not two (cf. `_seat_state`): confusing "disconnected" and
"orphan" leads to offering to release the seat of someone we can name — the view
only read LIVE bindings, so every disconnected seat showed as "orphan ·
no oto user".

`SUPER_ADMIN`: the inventory reveals cross-user ownership, the release is
irreversible. No secret leaves (the key is used to call unipile, never returned).

**The holder's `unipile` right** (oto-backend#806): the inventory says, per seat, whether
a binding holding it in service still has the right (`entitled`), since when it lost
it (`entitlement_lost_at`) and when the account will be deleted (`deletion_scheduled_at`).
The right is read for the binding's HOLDER (`sub` of the row) in the binding's org:
the org's, or a row set on the person (in the org or everywhere) — never for
the admin who is looking. The release accepts a seat in service for which NO holder
has the right any more: that is no longer cutting off the messaging of someone entitled to it, it is
advancing what the `unipile-fin-de-droit` job will do by itself
(`oto_mcp/unipile_fin_de_droit.py`). Both go through the same action, `liberer`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Optional

from pydantic import BaseModel

from .. import access, db
from ._authz import SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


class SeatsListInput(BaseModel):
    pass


class SeatReleaseInput(BaseModel):
    account_id: str
    # Release a seat IN SERVICE. Not by default: cutting the messaging of someone
    # who uses it without their having asked must be an EXPLICIT action, never the
    # default behaviour of a malformed call.
    force: bool = False


class Seat(BaseModel):
    account_id: str
    name: Optional[str] = None
    # `provider` is the v2 key; `type` repeats it for clients written before the
    # switch (it has been `null` there since, hence an empty "channel" column).
    provider: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    owner_sub: Optional[str] = None
    owner_email: Optional[str] = None
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    disconnected_at: Optional[str] = None
    state: str          # bound | disconnected | orphan
    orphan: bool        # = state == "orphan"
    # The `unipile` right of a seat holder in their org (any one, if it is
    # in service in several) — read from the declared rights (`access.has_right`).
    entitled: bool = False
    # First run of the end-of-right job that saw it without the right; None as long
    # as no run has marked it (or once the right has come back).
    entitlement_lost_at: Optional[str] = None
    # `entitlement_lost_at` + the delay (`OTO_UNIPILE_FIN_DE_DROIT_DELAI_JOURS`);
    # None if a holder has the right or the seat is not marked.
    deletion_scheduled_at: Optional[str] = None


class SeatsView(BaseModel):
    configured: bool
    instance_dsn: Optional[str] = None
    seats: list[Seat] = []
    orphan_count: int = 0
    # Seats we can stop paying for = orphans + disconnected. It is THIS number
    # that quantifies the saving, not `orphan_count`.
    reclaimable_count: int = 0


class SeatReleased(BaseModel):
    ok: bool
    account_id: str
    was: str            # the state the seat was in when it was released
    # LIVE bindings unlinked along the way — non-zero only on a `force`, and it is
    # the number of people whose messaging was cut.
    unbound: int = 0


def _platform_client():
    """Client on the unipile PLATFORM key (ADR 0044 §F: unified vault), or None
    if the platform has none under contract."""
    from .. import credentials_store
    insts = credentials_store.list_platform_instances("unipile")
    if not insts:
        return None
    api_key = credentials_store.get_credential(
        credentials_store.PLATFORM, insts[0]["label"], "unipile")
    from oto.tools.unipile import UnipileClient
    return UnipileClient(api_key=api_key)  # dsn=None → client default, api.unipile.com


def _seat_state(rows: list[dict]) -> str:
    """The state of a seat with regard to the oto bindings that NAME it.

    `bound` = at least one live binding (in service) · `disconnected` = only dead
    bindings (its owner disconnected it on the oto side, the seat still runs
    at unipile) · `orphan` = no row, nobody claims it."""
    if not rows:
        return "orphan"
    return "bound" if any(r["disconnected_at"] is None for r in rows) else "disconnected"


def _rows_for(account_id: str) -> list[dict]:
    return [r for r in db.unipile_account_owners(include_disconnected=True)
            if r["account_id"] == account_id]


def _droit_vivant(rows: list[dict], droits: Optional[dict] = None) -> bool:
    """Does a binding holding this seat IN SERVICE still have the `unipile` right?

    The right is that of the row's HOLDER (`r["sub"]`) in the row's org —
    the org's right, or a row set on the person —, never the caller's
    (an admin doing the cleanup is not the holder). Only live bindings
    count: an account adopted in two orgs stays due as long as just one of the two has
    the right. `droits` = `(sub, org_id) → bool` cache of one inventory, so that each
    holder is read in each org only once."""
    droits = {} if droits is None else droits
    for r in rows:
        org = r.get("org_id")
        if r.get("disconnected_at") is not None or org is None:
            continue
        cle = (r["sub"], int(org))
        if cle not in droits:
            droits[cle] = access.has_right(r["sub"], int(org), "unipile")
        if droits[cle]:
            return True
    return False


def liberer(client, account_id: str, rows: list[dict]) -> int:
    """THE action that takes a seat back: unlink its live bindings, then delete it
    at unipile. Returns the number of bindings unlinked. Raises if unipile refuses.

    Shared by `release` and by the end-of-right job: two paths to the
    same irreversible deletion, a single order.

    Unlink BEFORE, take back AFTER. Otherwise the account disappears at unipile while
    oto still believes it is linked: the org's seat cap counts a seat that no
    longer exists, and every view that reads the bindings (including the billing
    lens) announces a phantom connection. Soft-disconnect, not row deletion:
    the row survives as proof of ownership.

    If unipile fails, the bindings STAY unlinked: the org no longer serves this account, and
    a retry will redo the action — the seat is then `disconnected`, hence eligible
    without `force`. We do NOT relink: that would give back an access we just removed."""
    unbound = 0
    for r in rows:
        if r.get("disconnected_at") is None and r.get("org_id") is not None:
            db.clear_unipile_account(r["sub"], r["org_id"], r.get("provider") or "LINKEDIN")
            unbound += 1
    client.delete_account(account_id)
    return unbound


async def _list_seats(ctx: ResolvedCtx, inp: SeatsListInput) -> dict:
    # `_platform_client` reads the vault (SQL): off the event loop, like the two reads after it.
    client = await asyncio.to_thread(_platform_client)
    if client is None:
        return {"configured": False, "instance_dsn": None, "seats": [],
                "orphan_count": 0, "reclaimable_count": 0}
    try:
        instance = await asyncio.to_thread(client.list_accounts)
    except Exception as e:  # noqa: BLE001 — upstream outage, not an authz refusal
        raise AuthzDenied(502, "unipile_list_failed", str(e))
    owners: dict[str, list[dict]] = {}
    for r in await asyncio.to_thread(db.unipile_account_owners, include_disconnected=True):
        owners.setdefault(r["account_id"], []).append(r)
    from ..unipile_fin_de_droit import delai_jours
    delai = timedelta(days=delai_jours())
    droits: dict = {}
    seats = []
    for a in instance:
        rows = owners.get(a.get("id")) or []
        state = _seat_state(rows)
        # The owner to display: the live binding if there is one, otherwise the last
        # known — a dead row still NAMES the person to write to before releasing.
        best = next((r for r in rows if r["disconnected_at"] is None), None) or (
            sorted(rows, key=lambda r: str(r["connected_at"] or ""))[-1] if rows else None)
        # Out of service, the org of the last known binding answers.
        vus = rows if state == "bound" else ([{**best, "disconnected_at": None}]
                                              if best else [])
        entitled = await asyncio.to_thread(_droit_vivant, vus, droits)
        pertes = [r["entitlement_lost_at"] for r in rows if r.get("entitlement_lost_at")]
        perte = max(pertes) if pertes else None
        srcs = a.get("sources") or []
        provider = a.get("provider") or a.get("type")
        seats.append({
            "account_id": a.get("id"),
            "name": a.get("name"),
            "provider": provider,
            "type": provider,
            "status": a.get("status") or (srcs[0].get("status") if srcs else None) or "ok",
            "created_at": a.get("created_at"),
            "owner_sub": best["sub"] if best else None,
            "owner_email": best["email"] if best else None,
            "org_id": best["org_id"] if best else None,
            "org_name": best["org_name"] if best else None,
            "disconnected_at": best["disconnected_at"] if best else None,
            "state": state,
            "orphan": state == "orphan",
            "entitled": entitled,
            # Same shape as the other dates in the database (`db/_conn`, "YYYY-MM-DD hh:mm:ss").
            "entitlement_lost_at": perte,
            "deletion_scheduled_at": (
                (datetime.fromisoformat(perte) + delai).isoformat(sep=" ")
                if perte is not None and not entitled else None),
        })
    return {
        "configured": True,
        "instance_dsn": client.dsn,
        "seats": seats,
        "orphan_count": sum(1 for s in seats if s["state"] == "orphan"),
        "reclaimable_count": sum(1 for s in seats if s["state"] != "bound"),
    }


async def _release_seat(ctx: ResolvedCtx, inp: SeatReleaseInput) -> dict:
    rows = await asyncio.to_thread(_rows_for, inp.account_id)
    state = _seat_state(rows)
    if (state == "bound" and not inp.force
            and await asyncio.to_thread(_droit_vivant, rows)):
        # Releasing it would cut the messaging of someone who uses it AND is entitled
        # to it, without their having asked. That action belongs to its owner
        # (`DELETE /api/me/unipile`); the admin only cleans up behind. `force=true`
        # exists for the cases where the owner will NOT do it — deciding WHEN a seat
        # in service can be taken back belongs to the caller, not to this face.
        # Without the right, the seat is no longer used (lot 3 of #806): taking it back
        # only advances the deletion that the end-of-right job will do.
        raise AuthzDenied(409, "seat_in_use",
                          "This seat is in service and its holder has the right to "
                          "hosted messaging — they must disconnect it "
                          "first, or pass force=true to take it back anyway.")
    client = await asyncio.to_thread(_platform_client)
    if client is None:
        raise AuthzDenied(400, "no_platform_key", "No unipile platform key.")
    try:
        unbound = await asyncio.to_thread(liberer, client, inp.account_id, rows)
    except Exception as e:  # noqa: BLE001 — upstream outage, not an authz refusal
        # The bindings are already unlinked (cf. `liberer`): we do not relink.
        raise AuthzDenied(502, "unipile_delete_failed", str(e))
    logger.info("unipile seat taken back account_id=%s state=%s force=%s unbound=%d by=%s",
                inp.account_id, state, inp.force, unbound, ctx.sub)
    return {"ok": True, "account_id": inp.account_id, "was": state, "unbound": unbound}


CAPABILITIES += [
    Capability(
        key="admin.unipile_seats", handler=_list_seats, Input=SeatsListInput,
        authz=SUPER_ADMIN, Output=SeatsView,
        description=(
            "[super admin] Seats living on the shared unipile platform key, reconciled "
            "with their oto bindings. `state`: bound (in service) | disconnected (owner "
            "unhooked it on oto, the seat still bills) | orphan (nobody claims it). "
            "`entitled` = an org holding the seat still has the `unipile` right; "
            "`entitlement_lost_at` / `deletion_scheduled_at` = when it lost it, and "
            "when the account will be deleted on unipile if it does not come back. "
            "`reclaimable_count` = what you can stop paying for. No secret returned."),
        mcp=None,  # MCP face = op-aware console `oto_admin_unipile_seat`
        rest=RestBinding("GET", "/api/admin/unipile/seats"),
    ),
    Capability(
        key="admin.unipile_seat_release", handler=_release_seat, Input=SeatReleaseInput,
        authz=SUPER_ADMIN, Output=SeatReleased,
        description=(
            "[super admin] Frees a seat: deletes the account on unipile, so it stops "
            "billing. IRREVERSIBLE (the hosted session is destroyed; reconnecting yields "
            "a NEW account_id). Refuses a seat in service whose org still has the "
            "`unipile` right (409 seat_in_use) — that disconnection belongs to its owner "
            "— UNLESS `force: true`. A seat in service whose org LOST the right is freed "
            "without `force`. Either way every LIVE binding is soft-disconnected first "
            "(the ownership row survives, so a reconnection still rebinds "
            "deterministically), and only then is the seat freed. "
            "`unbound` = how many people's messaging was cut. Deciding WHEN a seat in "
            "service may be taken back belongs to the caller, never to this face."),
        mcp=None,  # MCP face = op-aware console `oto_admin_unipile_seat`
        rest=RestBinding("DELETE", "/api/admin/unipile/seats/{account_id}"),
    ),
]
