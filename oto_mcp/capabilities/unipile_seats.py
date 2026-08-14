"""Sièges de la clé PLATEFORME unipile : les inventorier, en libérer un (ADR 0009).

Un siège se paie tant qu'il **existe chez unipile** — se déconnecter côté oto ne le
rend pas (`clear_unipile_account` est un soft-disconnect : la ligne survit comme preuve
de propriété). Il y avait donc un inventaire mais aucun geste : le ménage passait par
un script sur la box, avec la clé plateforme en main. C'est ce que ces deux verbes
suppriment.

Trois états, et surtout pas deux (cf. `_seat_state`) : confondre « déconnecté » et
« orphelin » fait proposer de libérer le siège de quelqu'un qu'on sait nommer — la vue
ne lisait que les bindings VIVANTS, donc tout siège déconnecté s'affichait « orphelin ·
aucun user oto ».

`SUPER_ADMIN` : l'inventaire révèle l'ownership cross-user, la libération est
irréversible. Aucun secret ne sort (la clé sert à appeler unipile, jamais rendue).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pydantic import BaseModel

from .. import db
from ._authz import SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


class SeatsListInput(BaseModel):
    pass


class SeatReleaseInput(BaseModel):
    account_id: str


class Seat(BaseModel):
    account_id: str
    name: Optional[str] = None
    # `provider` est la clé v2 ; `type` la redit pour les clients écrits avant la
    # bascule (elle y valait `null` depuis, d'où une colonne « canal » vide).
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


class SeatsView(BaseModel):
    configured: bool
    instance_dsn: Optional[str] = None
    seats: list[Seat] = []
    orphan_count: int = 0
    # Sièges qu'on peut cesser de payer = orphelins + déconnectés. C'est CE nombre
    # qui chiffre l'économie, pas `orphan_count`.
    reclaimable_count: int = 0


class SeatReleased(BaseModel):
    ok: bool
    account_id: str
    was: str            # l'état qu'avait le siège au moment de le libérer


def _platform_client():
    """Client sur la clé PLATEFORME unipile (ADR 0044 §F : coffre unifié), ou None
    si la plateforme n'en contracte aucune."""
    from .. import credentials_store
    insts = credentials_store.list_platform_instances("unipile")
    if not insts:
        return None
    api_key = credentials_store.get_credential(
        credentials_store.PLATFORM, insts[0]["label"], "unipile")
    from oto.tools.unipile import UnipileClient
    return UnipileClient(api_key=api_key)  # dsn=None → env/api.unipile.com


def _seat_state(rows: list[dict]) -> str:
    """L'état d'un siège au regard des bindings oto qui le NOMMENT.

    `bound` = au moins un binding vivant (en service) · `disconnected` = que des
    bindings morts (son propriétaire l'a déconnecté côté oto, le siège court toujours
    chez unipile) · `orphan` = aucune ligne, personne ne le réclame."""
    if not rows:
        return "orphan"
    return "bound" if any(r["disconnected_at"] is None for r in rows) else "disconnected"


def _rows_for(account_id: str) -> list[dict]:
    return [r for r in db.unipile_account_owners(include_disconnected=True)
            if r["account_id"] == account_id]


async def _list_seats(ctx: ResolvedCtx, inp: SeatsListInput) -> dict:
    client = _platform_client()
    if client is None:
        return {"configured": False, "instance_dsn": None, "seats": [],
                "orphan_count": 0, "reclaimable_count": 0}
    try:
        instance = await asyncio.to_thread(client.list_accounts)
    except Exception as e:  # noqa: BLE001 — panne amont, pas un refus d'autz
        raise AuthzDenied(502, "unipile_list_failed", str(e))
    owners: dict[str, list[dict]] = {}
    for r in db.unipile_account_owners(include_disconnected=True):
        owners.setdefault(r["account_id"], []).append(r)
    seats = []
    for a in instance:
        rows = owners.get(a.get("id")) or []
        state = _seat_state(rows)
        # Le propriétaire à afficher : le binding vivant s'il existe, sinon le dernier
        # connu — une ligne morte NOMME encore la personne à qui écrire avant de libérer.
        best = next((r for r in rows if r["disconnected_at"] is None), None) or (
            sorted(rows, key=lambda r: str(r["connected_at"] or ""))[-1] if rows else None)
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
        })
    return {
        "configured": True,
        "instance_dsn": client.dsn,
        "seats": seats,
        "orphan_count": sum(1 for s in seats if s["state"] == "orphan"),
        "reclaimable_count": sum(1 for s in seats if s["state"] != "bound"),
    }


async def _release_seat(ctx: ResolvedCtx, inp: SeatReleaseInput) -> dict:
    state = _seat_state(_rows_for(inp.account_id))
    if state == "bound":
        # Le libérer couperait la messagerie de quelqu'un qui s'en sert, sans qu'il
        # l'ait demandé. Ce geste-là appartient à son propriétaire (`DELETE
        # /api/me/unipile`) ; l'admin ne fait que le ménage derrière.
        raise AuthzDenied(409, "seat_in_use",
                          "Ce siège est en service — son propriétaire doit le déconnecter d'abord.")
    client = _platform_client()
    if client is None:
        raise AuthzDenied(400, "no_platform_key", "Aucune clé plateforme unipile.")
    try:
        await asyncio.to_thread(client.delete_account, inp.account_id)
    except Exception as e:  # noqa: BLE001 — panne amont, pas un refus d'autz
        raise AuthzDenied(502, "unipile_delete_failed", str(e))
    logger.info("unipile seat libéré account_id=%s état=%s par=%s",
                inp.account_id, state, ctx.sub)
    return {"ok": True, "account_id": inp.account_id, "was": state}


CAPABILITIES += [
    Capability(
        key="admin.unipile_seats", handler=_list_seats, Input=SeatsListInput,
        authz=SUPER_ADMIN, Output=SeatsView,
        description=(
            "[super admin] Seats living on the shared unipile platform key, reconciled "
            "with their oto bindings. `state`: bound (in service) | disconnected (owner "
            "unhooked it on oto, the seat still bills) | orphan (nobody claims it). "
            "`reclaimable_count` = what you can stop paying for. No secret returned."),
        mcp=None,  # face MCP = console op-aware `oto_admin_unipile_seat`
        rest=RestBinding("GET", "/api/admin/unipile/seats"),
    ),
    Capability(
        key="admin.unipile_seat_release", handler=_release_seat, Input=SeatReleaseInput,
        authz=SUPER_ADMIN, Output=SeatReleased,
        description=(
            "[super admin] Frees a seat: deletes the account on unipile, so it stops "
            "billing. IRREVERSIBLE (the hosted session is destroyed; reconnecting yields "
            "a NEW account_id). Refuses a seat still in service (409 seat_in_use) — that "
            "disconnection belongs to its owner."),
        mcp=None,  # face MCP = console op-aware `oto_admin_unipile_seat`
        rest=RestBinding("DELETE", "/api/admin/unipile/seats/{account_id}"),
    ),
]
