"""Capacité : les modèles qu'une org ÉTEINT pour ses agents (`orgs.disabled_models`).

Un interrupteur par modèle du catalogue (`runner_models.MODELES`), hors abonnements
personnels (`sub:*` : chacun le sien, rien à éteindre pour l'org). Tous allumés par
défaut. Éteint par un admin de l'org : plus proposé (`allowed: false, disabled: true` au
catalogue, jamais par défaut), plus posable (`model_disabled`), un agent déjà dessus part
sur le défaut de l'org (`_modele.charge_de_l_org`), et un travail déjà en file sur lui est
arrêté à la réservation, raison écrite (`runner_jobs._avec_cle`).

⚠️ Le modèle par DÉFAUT de l'org ne s'éteint pas (`model_is_default`) : c'est sur lui que
partent les agents des modèles éteints. Éteindre d'abord un autre ne change rien à cette
règle ; le défaut se redérive (`_modele.etat_servi`), donc éteindre l'actuel défaut exige
d'en avoir un autre — un geste à part que l'écran ne propose pas.

Lecture = membre ; écriture = org_admin. Une déclaration → deux surfaces : REST
`/api/orgs/{id}/models` ici, MCP par la console `oto_org_settings domain=models`
(`org_console.py`).
"""
from __future__ import annotations

from pydantic import BaseModel

from ... import db, runner_models
from .. import _modele
from .._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}
_PATH = "/api/orgs/{id}/models"


class GetModelsInput(BaseModel):
    org_id: int


class SetModelInput(BaseModel):
    org_id: int
    model: str
    enabled: bool


class ModelesDeLOrg(BaseModel):
    org_id: int
    #: Les ids des modèles éteints par un admin de l'org, triés. Vide = tous allumés.
    disabled: list[str]


def lire(org_id: int) -> dict:
    eteints = db.modeles_eteints.eteints(org_id)
    if eteints is None:
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue.")
    return {"org_id": org_id, "disabled": eteints}


def basculer(org_id: int, model: str, allume: bool) -> dict:
    """Allume ou éteint UN modèle pour l'org. Partagé par le REST et la console MCP."""
    m = next((x for x in runner_models.MODELES if x.id == model), None)
    if m is None or m.family in runner_models.FAMILLES_PERSONNELLES:
        connus = ", ".join(x.id for x in runner_models.MODELES
                           if x.family not in runner_models.FAMILLES_PERSONNELLES)
        raise AuthzDenied(400, "unknown_model",
                          f"`{model}` n'est pas un modèle que l'org peut allumer ou éteindre "
                          f"(modèles : {connus}).")
    lire(org_id)   # 404 d'abord : une org inconnue n'a pas de défaut à protéger
    if not allume:
        etat = _modele.etat_servi(db.runner_arme(org_id), org_id)
        defaut = next((x["id"] for x in etat.get("models") or [] if x.get("default")), None)
        if defaut == model:
            raise AuthzDenied(
                409, "model_is_default",
                f"`{model}` est le modèle par défaut de l'organisation : c'est sur lui que "
                "partent les agents des modèles éteints. Il reste allumé.")
    if not db.modeles_eteints.poser(org_id, model, allume):
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue.")
    return lire(org_id)


def _get(ctx: ResolvedCtx, inp: GetModelsInput) -> dict:
    return lire(inp.org_id)


def _set(ctx: ResolvedCtx, inp: SetModelInput) -> dict:
    return basculer(inp.org_id, inp.model, inp.enabled)


_ERREURS_GET = (DeclaredError(404, "unknown_org", "l'org n'existe pas"),)
_ERREURS_SET = (
    DeclaredError(404, "unknown_org", "l'org n'existe pas"),
    DeclaredError(400, "unknown_model", "le modèle n'est pas au catalogue, ou c'est un "
                  "abonnement personnel"),
    DeclaredError(409, "model_is_default", "le modèle par défaut de l'org ne s'éteint pas"),
)

CAPABILITIES += [
    Capability(
        key="org.models.get", handler=_get, Input=GetModelsInput,
        authz=ORG_MEMBER_OF("org_id"), Output=ModelesDeLOrg, errors=_ERREURS_GET,
        description=("Read the models the org turned off for its agents: `disabled` = their "
                     "ids (`runner.models[].id`). Empty by default: every model is on."),
        rest=RestBinding("GET", _PATH, _ID),
    ),
    Capability(
        key="org.models.set", handler=_set, Input=SetModelInput,
        authz=ORG_ADMIN_OF("org_id"), Output=ModelesDeLOrg, errors=_ERREURS_SET,
        description=("Turn one model on or off for this org's agents (`model`, `enabled`). "
                     "Off: no longer offered (`allowed: false, disabled: true`), an agent "
                     "cannot be set on it (`model_disabled`), an agent already on it runs "
                     "on the org's default model instead, and a job already queued on it "
                     "is stopped at the claim with the reason written. The org's default "
                     "model cannot be turned off (`model_is_default`); personal "
                     "subscription models are not switchable. Org admin."),
        rest=RestBinding("PUT", _PATH, _ID),
    ),
]
