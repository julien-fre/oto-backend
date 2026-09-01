"""Capacité « flottes du runner » — la configuration déclarée d'un passage (R4).

Deux faces, et la frontière n'est pas celle de `runner.jobs`. La file de jobs est
worker-only : c'est de la plomberie d'exécution, elle n'a pas de face agent. Une
FLOTTE est de la config utilisateur — « fais tourner cette procédure sur ce
tableau, dans ce périmètre, jusqu'à telle borne » — au même titre qu'un
déclencheur. Elle se pose en conversation comme au dashboard, et surtout elle se
LIT : l'état d'un passage n'existait jusqu'ici que parce qu'une session poussait
des messages à une autre.

⚠️ **Un lancement vise une flotte déclarée, jamais un tableau passé en argument.**
Un verbe généraliste rendrait accessible en un appel le geste qu'on a passé deux
jours à empêcher — lancer des agents sur le fichier d'un client, sans cible
constatée, sans périmètre, sans borne. Et l'argument porte plus loin que le
risque : **une configuration déclarée est l'endroit où les gardes VIVENT.** Un
lancement libre n'a nulle part où accrocher une cible ni un plafond. Déclarer
n'est pas restreindre : c'est donner un domicile.

⚠️ **La CIBLE ne se modifie pas.** `namespace` et `row_filter` sont figés à la
déclaration : rediriger un passage en vol vers un autre tableau est précisément
ce contre quoi la déclaration existe. Un autre tableau, c'est une autre flotte —
et cette frontière compte d'autant plus que les bancs d'essai et la production
d'un client ne vivent pas dans la même org.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import db
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ARRETS = ("stopped", "done", "failed")


class FleetInput(BaseModel):
    op: Literal["create", "list", "get", "state", "update", "stop"]
    fleet_id: Optional[int] = None
    status: Optional[str] = None
    # create —
    label: Optional[str] = None
    procedure: Optional[str] = None
    tools: Optional[list[str]] = None
    namespace: Optional[str] = None
    row_filter: Optional[dict] = None
    project_id: Optional[int] = None
    input: Optional[str] = None
    max_steps: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    workers: Optional[int] = None
    max_rows: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_consecutive_failures: Optional[int] = None
    max_tokens_per_row: Optional[int] = None
    # stop —
    reason: Optional[str] = None


class Fleet(BaseModel):
    """Une flotte telle que servie (les colonnes de `_COLS`, db/runner_fleets)."""
    id: int
    org_id: Optional[int] = None
    sub: Optional[str] = None
    label: Optional[str] = None
    procedure: Optional[str] = None
    project_id: Optional[int] = None
    tools: Optional[list[str]] = None
    input: Optional[str] = None
    max_steps: Optional[int] = None
    namespace: Optional[str] = None
    row_filter: Optional[dict] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    workers: Optional[int] = None
    max_rows: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_consecutive_failures: Optional[int] = None
    max_tokens_per_row: Optional[int] = None
    status: Optional[str] = None
    stop_reason: Optional[str] = None
    started_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    stopped_at: Optional[str] = None
    created_at: Optional[str] = None


class FleetState(BaseModel):
    """L'avancement d'un passage, agrégé sur ses travaux.

    `aucun_travail_rattache` est DÉCLARÉ plutôt que déduit de compteurs à zéro :
    un zéro qui peut vouloir dire « rien trouvé » ou « personne n'a regardé » est
    le défaut qui a coûté le plus cher sur ce chantier.
    """
    jobs_total: int
    pending: Optional[int] = None
    claimed: Optional[int] = None
    done: Optional[int] = None
    failed: Optional[int] = None
    abandonnes: Optional[int] = None
    usage_tokens: Optional[int] = None
    max_tokens_ligne: Optional[int] = None
    dernier_fini: Optional[str] = None
    aucun_travail_rattache: bool


class FleetOut(BaseModel):
    fleet: Optional[Fleet] = None
    fleets: Optional[list[Fleet]] = None
    state: Optional[FleetState] = None


def _fleets(ctx: ResolvedCtx, inp: FleetInput) -> dict:
    if not ctx.org_id:
        raise AuthzDenied(400, "org_required", "les flottes sont org-scopées")

    if inp.op == "create":
        manquants = [c for c in ("label", "procedure", "tools") if not getattr(inp, c)]
        if manquants:
            raise AuthzDenied(
                400, "missing_fields",
                f"create exige : {', '.join(manquants)} — le nom du passage, la "
                "procédure à jouer, et les outils (l'allowlist du run)")
        # La cible se DÉCLARE ou s'assume absente : un passage qui écrit dans un
        # tableau sans l'avoir nommé n'a aucun périmètre à opposer à un agent.
        if inp.row_filter is not None and not inp.namespace:
            raise AuthzDenied(
                400, "target_incomplete",
                "`row_filter` sans `namespace` : un périmètre suppose un tableau. "
                "Nomme la cible, ou n'en déclare aucune.")
        return {"fleet": db.create_fleet(
            ctx.org_id, ctx.sub, label=inp.label, procedure=inp.procedure,
            tools=inp.tools, namespace=inp.namespace, row_filter=inp.row_filter,
            project_id=inp.project_id, input=inp.input, max_steps=inp.max_steps,
            provider=inp.provider, model=inp.model, workers=inp.workers or 1,
            max_rows=inp.max_rows, max_cost_usd=inp.max_cost_usd,
            max_consecutive_failures=inp.max_consecutive_failures,
            max_tokens_per_row=inp.max_tokens_per_row)}

    if inp.op == "list":
        return {"fleets": db.list_fleets(ctx.org_id, inp.status)}

    if inp.fleet_id is None:
        raise AuthzDenied(400, "missing_fields", f"{inp.op} exige `fleet_id`")

    if inp.op == "get":
        f = db.get_fleet(inp.fleet_id, ctx.org_id)
        if not f:
            raise AuthzDenied(404, "fleet_not_found", "flotte inconnue")
        return {"fleet": f}

    if inp.op == "state":
        etat = db.fleet_state(inp.fleet_id, ctx.org_id)
        if not etat:
            raise AuthzDenied(404, "fleet_not_found", "flotte inconnue")
        return etat

    if inp.op == "stop":
        f = db.set_status(inp.fleet_id, ctx.org_id, "stopped",
                          inp.reason or "arrêt demandé")
        if not f:
            raise AuthzDenied(404, "fleet_not_found", "flotte inconnue")
        return {"fleet": f}

    # update — partiel, et jamais sur la cible ni sur l'état.
    champs: dict[str, Any] = {c: getattr(inp, c) for c in db.CHAMPS_MODIFIABLES
                              if getattr(inp, c) is not None}
    if inp.namespace is not None or inp.row_filter is not None:
        raise AuthzDenied(
            400, "target_is_frozen",
            "la cible d'un passage ne se modifie pas — `namespace` et `row_filter` "
            "sont figés à la déclaration. Un autre tableau, c'est une autre flotte.")
    f = db.update_fleet(inp.fleet_id, ctx.org_id, champs)
    if not f:
        raise AuthzDenied(404, "fleet_not_found", "flotte inconnue")
    return {"fleet": f}


CAPABILITIES += [
    Capability(
        key="runner.fleets",
        handler=_fleets,
        Input=FleetInput,
        Output=FleetOut,
        authz=ORG_MEMBER,
        mcp="oto_fleet",
        rest=RestBinding(verb="POST", path="/api/me/runner/fleets"),
        description=(
            "Declared configuration of an agent PASS — what a fleet runs, on which "
            "table, within which perimeter, and up to which limit. op=create "
            "(`label` + procedure slug + `tools` allowlist ; optional target "
            "`namespace` + `row_filter`, execution context `provider`/`model`, and "
            "limits `max_rows` / `max_cost_usd` / `max_consecutive_failures` / "
            "`max_tokens_per_row`) / list (optionally filtered by `status`) / get / "
            "state / update / stop. op=state returns the pass PROGRESS aggregated "
            "over its jobs — pending, claimed, done, failed, abandoned, tokens "
            "consumed, largest single row — and says `aucun_travail_rattache` "
            "explicitly rather than returning zeros you would read as 'nothing "
            "happened'. The TARGET is frozen at declaration: redirecting a running "
            "pass to another table is what declaring exists to prevent — declare "
            "another fleet instead. Launching belongs to the scheduler; this "
            "capability declares, reads and stops."
        ),
    ),
]
