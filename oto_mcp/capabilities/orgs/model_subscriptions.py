"""Capacité : le PLAFOND de consommation des abonnements, réglé par l'org.

Un agent de famille `claude_subscription` tourne sur l'abonnement PERSONNEL de son
porteur (`capabilities/_abonnement`). L'org règle la part maximale, en %, de l'usage
TOTAL du compte du fournisseur (fenêtres cinq heures et sept jours, usage perso
compris) que ses travaux peuvent atteindre : au-delà, les travaux suivants ATTENDENT
la réinitialisation — le run en cours finit toujours. Sans réglage, le défaut du code
(`_abonnement.DEFAUT_LIMITE_PCT`). Une personne peut resserrer pour elle-même
(`PATCH /api/me/model-subscriptions/{family}`), jamais relâcher : le seuil effectif
est le min des deux, calculé par `_abonnement.seuil` et nulle part ailleurs.

Le MODE de l'org, sur la même ressource : `personnel` (défaut — chaque travail sur
l'abonnement de son demandeur) ou `pool` (sur l'abonnement d'un membre qui l'a prêté à
l'org, flottes comprises ; `org_subscription_pool`). Réglé à part
(`PUT …/{family}/mode`) : un plafond et un mode ne se règlent pas dans le même geste,
et un corps partiel ne dirait pas lequel des deux on voulait laisser.

Lecture = membre ; écriture = org_admin. Une déclaration → deux surfaces : REST
`/api/orgs/{id}/model-subscriptions/{family}` ici, MCP par la console
`oto_org_settings domain=model_subscriptions` (`org_console.py`).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ... import org_store
from ...db import org_subscription_limits, org_subscription_pool
from .. import _abonnement
from .._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx,
                      RestBinding)
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}
_PATH = "/api/orgs/{id}/model-subscriptions/{family}"
_PATH_MODE = "/api/orgs/{id}/model-subscriptions/{family}/mode"


class OrgPlafond(BaseModel):
    """Le plafond de l'org pour une famille d'abonnement."""
    org_id: int
    family: str
    limit_pct: int = Field(
        description=("The cap, in % of each member's provider account TOTAL usage "
                     "(5-hour and 7-day windows). Past it, the org's next jobs on that "
                     "subscription wait for the window to reset."))
    default: bool = Field(
        description="`true` = the org has set nothing; this is the platform default.")
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    mode: str = Field(
        description=("`personnel` (default): each job runs on the subscription of the "
                     "member who asked for it. `pool`: the org's jobs — fleets included "
                     "— run on the subscription of a member who LENT theirs to this org "
                     "(`PATCH /api/me/model-subscriptions/{family}` `lent_to`), the "
                     "least recently used free one first."))
    pool_size: int = Field(
        description=("How many members currently lend this org a usable (connected) "
                     "subscription. In `pool` mode, zero means the org's jobs wait."))


class GetOrgPlafondInput(BaseModel):
    org_id: int
    family: str


class SetOrgPlafondInput(BaseModel):
    org_id: int
    family: str
    # Requis, nullable : l'omettre est une erreur de forme, `null` revient au défaut.
    limit_pct: Optional[int] = Field(
        ..., description="1..100, or `null` to go back to the platform default.")


class SetOrgModeInput(BaseModel):
    org_id: int
    family: str
    mode: Literal["personnel", "pool"] = Field(
        description="`personnel` (each requester's own subscription) or `pool`.")


def _exiger(org_id: int, famille: str) -> None:
    if not org_store.get_org(org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue.")
    if not _abonnement.est_abonnement(famille):
        raise AuthzDenied(
            400, "unknown_family",
            f"`{famille}` n'est pas une famille servie par abonnement "
            f"({', '.join(sorted(_abonnement.FAMILLES))}).")


def _servi(org_id: int, famille: str) -> dict:
    pool = {"mode": ((org_subscription_pool.get_mode(org_id, famille) or {}).get("mode")
                     or org_subscription_pool.PERSONNEL),
            "pool_size": org_subscription_pool.taille_du_pool(org_id, famille)}
    ligne = org_subscription_limits.get_limite(org_id, famille)
    if not ligne:
        return {"org_id": org_id, "family": famille,
                "limit_pct": _abonnement.DEFAUT_LIMITE_PCT, "default": True,
                "updated_at": None, "updated_by": None, **pool}
    quand = ligne.get("updated_at")
    return {"org_id": org_id, "family": famille, "limit_pct": ligne["limite_pct"],
            "default": False,
            "updated_at": quand.isoformat() if hasattr(quand, "isoformat") else quand,
            "updated_by": ligne.get("updated_by"), **pool}


# `def`, pas `async def` : I/O bloquante (psycopg), servie en threadpool
# (`docs/event-loop-perf.md`).
def _get_plafond(ctx: ResolvedCtx, inp: GetOrgPlafondInput) -> dict:
    _exiger(inp.org_id, inp.family)
    return _servi(inp.org_id, inp.family)


def _set_plafond(ctx: ResolvedCtx, inp: SetOrgPlafondInput) -> dict:
    _exiger(inp.org_id, inp.family)
    _abonnement.exiger_limite_valide(inp.limit_pct)
    if inp.limit_pct is None:
        org_subscription_limits.retirer_limite(inp.org_id, inp.family)
    else:
        org_subscription_limits.poser_limite(inp.org_id, inp.family, inp.limit_pct,
                                             ctx.sub)
    return _servi(inp.org_id, inp.family)


def _set_mode(ctx: ResolvedCtx, inp: SetOrgModeInput) -> dict:
    """Le MODE de l'org. Il vaut pour le travail SUIVANT : un run en cours finit sur
    l'abonnement qui l'a pris. Passer en `pool` sans prêteur est permis (les membres
    prêtent ensuite) — `pool_size` le dit, et la pose d'un agent le refuse tant qu'il
    est nul."""
    _exiger(inp.org_id, inp.family)
    org_subscription_pool.poser_mode(inp.org_id, inp.family, inp.mode, ctx.sub)
    return _servi(inp.org_id, inp.family)


_ERREURS = (
    DeclaredError(404, "unknown_org", "l'org n'existe pas"),
    DeclaredError(400, "unknown_family", "une famille qui n'est pas servie par abonnement"),
)

CAPABILITIES += [
    Capability(
        key="org.model_subscriptions.get", handler=_get_plafond, Input=GetOrgPlafondInput,
        authz=ORG_MEMBER_OF("org_id"), Output=OrgPlafond, errors=_ERREURS,
        description=("Read the org's consumption cap on its members' personal model "
                     "subscriptions (e.g. `claude_subscription`): the max share, in %, "
                     "of each member's provider account TOTAL usage (5-hour and 7-day "
                     "windows) the org's jobs may reach before the next ones wait for "
                     "the reset. `default: true` = not set, platform default (80). A "
                     "member may set a LOWER cap for themselves; the lower one applies. "
                     "Also returns the org's `mode` (`personnel` | `pool`) and its "
                     "`pool_size` (members lending a usable subscription)."),
        rest=RestBinding("GET", _PATH, _ID),
    ),
    Capability(
        key="org.model_subscriptions.set", handler=_set_plafond, Input=SetOrgPlafondInput,
        authz=ORG_ADMIN_OF("org_id"), Output=OrgPlafond,
        errors=_ERREURS + (DeclaredError(400, "invalid_limit", "`limit_pct` hors de 1..100"),),
        description=("Set the org's consumption cap on its members' personal model "
                     "subscriptions: `limit_pct` 1..100 (% of each member's provider "
                     "account TOTAL usage, 5-hour and 7-day windows alike), or `null` to "
                     "go back to the platform default (80). Applies from the next job "
                     "report; a running job is never cut. Org admin."),
        rest=RestBinding("PUT", _PATH, _ID),
    ),
    Capability(
        key="org.model_subscriptions.set_mode", handler=_set_mode, Input=SetOrgModeInput,
        authz=ORG_ADMIN_OF("org_id"), Output=OrgPlafond, errors=_ERREURS,
        description=("Set how the org's jobs on a personal model subscription family "
                     "(e.g. `claude_subscription`) are paid for. `personnel` (default): "
                     "each job runs on the subscription of the member who asked for it; "
                     "fleets are refused. `pool`: jobs — fleets included — run on the "
                     "subscription of a member who explicitly LENT theirs to this org, "
                     "the least recently used free one first; with no lender available "
                     "they wait, none fail. Each lender's own cap applies to their "
                     "account (the lower of the org's cap and theirs). Applies from the "
                     "next job; a running job is never cut. Org admin."),
        rest=RestBinding("PUT", _PATH_MODE, _ID),
    ),
]
