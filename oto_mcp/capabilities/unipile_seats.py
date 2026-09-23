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

**Le droit `unipile` de l'org** (oto-backend#806) : l'inventaire dit, par siège, si une
org qui le tient en service a encore le droit (`entitled`), depuis quand elle l'a perdu
(`entitlement_lost_at`) et quand le compte sera supprimé (`deletion_scheduled_at`). La
libération accepte un siège en service dont AUCUNE org n'a plus le droit : ce n'est plus
couper la messagerie de quelqu'un qui y a droit, c'est avancer ce que le travail
`unipile-fin-de-droit` fera de lui-même (`oto_mcp/unipile_fin_de_droit.py`). Les deux
passent par le même geste, `liberer`.
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
    # Libérer un siège EN SERVICE. Par défaut non : couper la messagerie de quelqu'un
    # qui s'en sert sans qu'il l'ait demandé doit être un geste EXPLICITE, jamais le
    # comportement par défaut d'un appel mal formé.
    force: bool = False


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
    # Le droit `unipile` de l'org qui tient le siège (n'importe laquelle, s'il est en
    # service dans plusieurs) — lu dans les droits déclarés (`access.org_has`).
    entitled: bool = False
    # Premier passage du travail de fin de droit qui l'a vue sans droit ; None tant
    # qu'aucun passage ne l'a marquée (ou que le droit est revenu).
    entitlement_lost_at: Optional[str] = None
    # `entitlement_lost_at` + le délai (`OTO_UNIPILE_FIN_DE_DROIT_DELAI_JOURS`) ;
    # None si l'org a le droit ou n'est pas marquée.
    deletion_scheduled_at: Optional[str] = None


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
    # Bindings VIVANTS déliés au passage — non nul seulement sur un `force`, et c'est
    # le nombre de personnes dont la messagerie a été coupée.
    unbound: int = 0


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


def _droit_vivant(rows: list[dict], droits: Optional[dict] = None) -> bool:
    """Une org qui tient ce siège EN SERVICE a-t-elle encore le droit `unipile` ?

    Seuls les bindings vivants comptent : un compte adopté dans deux orgs reste dû
    tant qu'une seule des deux a le droit. `droits` = cache `org_id → bool` d'un
    inventaire, pour ne relire chaque org qu'une fois."""
    droits = {} if droits is None else droits
    for r in rows:
        org = r.get("org_id")
        if r.get("disconnected_at") is not None or org is None:
            continue
        if org not in droits:
            droits[org] = access.org_has(int(org), "unipile")
        if droits[org]:
            return True
    return False


def liberer(client, account_id: str, rows: list[dict]) -> int:
    """LE geste qui reprend un siège : délier ses bindings vivants, puis le supprimer
    chez unipile. Rend le nombre de bindings déliés. Lève si unipile refuse.

    Partagé par `release` et par le travail de fin de droit : deux chemins vers la
    même suppression irréversible, un seul ordre.

    Délier AVANT, reprendre APRÈS. Sans ça le compte disparaît chez unipile pendant
    qu'oto le croit encore lié : le plafond de sièges de l'org compte un siège qui
    n'existe plus, et toute vue qui lit les bindings (dont la lentille de
    facturation) annonce une connexion fantôme. Soft-déconnexion, pas suppression
    de ligne : elle survit comme preuve de propriété.

    Si unipile échoue, les bindings RESTENT déliés : l'org ne sert plus ce compte, et
    un rappel reprendra le geste — le siège est alors `disconnected`, donc éligible
    sans `force`. On ne relie PAS : ce serait rendre un accès qu'on vient de retirer."""
    unbound = 0
    for r in rows:
        if r.get("disconnected_at") is None and r.get("org_id") is not None:
            db.clear_unipile_account(r["sub"], r["org_id"], r.get("provider") or "LINKEDIN")
            unbound += 1
    client.delete_account(account_id)
    return unbound


async def _list_seats(ctx: ResolvedCtx, inp: SeatsListInput) -> dict:
    # `_platform_client` lit le coffre (SQL) : hors de la boucle, comme les deux lectures d'après.
    client = await asyncio.to_thread(_platform_client)
    if client is None:
        return {"configured": False, "instance_dsn": None, "seats": [],
                "orphan_count": 0, "reclaimable_count": 0}
    try:
        instance = await asyncio.to_thread(client.list_accounts)
    except Exception as e:  # noqa: BLE001 — panne amont, pas un refus d'autz
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
        # Le propriétaire à afficher : le binding vivant s'il existe, sinon le dernier
        # connu — une ligne morte NOMME encore la personne à qui écrire avant de libérer.
        best = next((r for r in rows if r["disconnected_at"] is None), None) or (
            sorted(rows, key=lambda r: str(r["connected_at"] or ""))[-1] if rows else None)
        # Hors service, c'est l'org du dernier binding connu qui répond.
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
            # Même forme que les autres dates de la base (`db/_conn`, « AAAA-MM-JJ hh:mm:ss »).
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
        # Le libérer couperait la messagerie de quelqu'un qui s'en sert ET qui y a
        # droit, sans qu'il l'ait demandé. Ce geste-là appartient à son propriétaire
        # (`DELETE /api/me/unipile`) ; l'admin ne fait que le ménage derrière.
        # `force=true` existe pour les cas où le propriétaire ne le fera PAS — décider
        # QUAND un siège en service peut être repris appartient à l'appelant, pas à
        # cette face. Sans droit, le siège ne sert plus (lot 3 de #806) : le reprendre
        # n'est qu'avancer la suppression que le travail de fin de droit fera.
        raise AuthzDenied(409, "seat_in_use",
                          "Ce siège est en service et son organisation a le droit à la "
                          "messagerie hébergée — son propriétaire doit le déconnecter "
                          "d'abord, ou passer force=true pour le reprendre quand même.")
    client = await asyncio.to_thread(_platform_client)
    if client is None:
        raise AuthzDenied(400, "no_platform_key", "Aucune clé plateforme unipile.")
    try:
        unbound = await asyncio.to_thread(liberer, client, inp.account_id, rows)
    except Exception as e:  # noqa: BLE001 — panne amont, pas un refus d'autz
        # Les bindings sont déjà déliés (cf. `liberer`) : on ne relie pas.
        raise AuthzDenied(502, "unipile_delete_failed", str(e))
    logger.info("unipile seat repris account_id=%s état=%s force=%s déliés=%d par=%s",
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
        mcp=None,  # face MCP = console op-aware `oto_admin_unipile_seat`
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
        mcp=None,  # face MCP = console op-aware `oto_admin_unipile_seat`
        rest=RestBinding("DELETE", "/api/admin/unipile/seats/{account_id}"),
    ),
]
