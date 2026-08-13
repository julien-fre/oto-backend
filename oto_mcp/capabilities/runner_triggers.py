"""Capacité « déclencheurs du runner » — la config qui fabrique des jobs (R3).

Deux faces, et c'est un choix de doctrine : un déclencheur est de la CONFIG
utilisateur, pas de la plomberie worker. « Tous les matins à 8h05, joue la
veille » doit pouvoir se poser EN CONVERSATION (`oto_trigger`) comme au
dashboard — c'est le `/schedule` du produit. La file de jobs, elle, reste
worker-only (`runner.jobs`, REST seul) : la frontière passe entre configurer
et exécuter.

Le FUSEAU se déclare, il ne se suppose pas : `tz` (défaut `Europe/Paris`,
écrit) — « 8h » doit dire quel 8h, sinon l'heure d'été décale toutes les
veilles d'une heure sans un mot. La validation (cron, fuseau, cadence
plancher) vit dans `runner_tick.validate_cron`, le même module qui calcule
les échéances : une seule vérité.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import db, runner_tick
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_TZ_DEFAUT = "Europe/Paris"


class TriggerInput(BaseModel):
    op: Literal["create", "list", "get", "update", "delete"]
    trigger_id: Optional[int] = None
    # create / update —
    procedure: Optional[str] = None
    cron: Optional[str] = None
    tz: Optional[str] = None
    tools: Optional[list[str]] = None
    project_id: Optional[int] = None
    input: Optional[str] = None
    label: Optional[str] = None
    max_steps: Optional[int] = None
    enabled: Optional[bool] = None


class TriggerOut(BaseModel):
    trigger: Optional[dict[str, Any]] = None
    triggers: Optional[list[dict[str, Any]]] = None
    ok: Optional[bool] = None


def _triggers(ctx: ResolvedCtx, inp: TriggerInput) -> dict:
    if not ctx.org_id:
        raise AuthzDenied(400, "org_required", "les déclencheurs sont org-scopés")

    if inp.op == "create":
        manquants = [c for c in ("procedure", "cron", "tools") if not getattr(inp, c)]
        if manquants:
            raise AuthzDenied(400, "missing_fields",
                              f"create exige : {', '.join(manquants)} — la procédure à "
                              "jouer, quand, et avec quels outils (l'allowlist du run)")
        tz = inp.tz or _TZ_DEFAUT
        try:
            runner_tick.validate_cron(inp.cron, tz)
        except ValueError as e:
            raise AuthzDenied(400, "invalid_schedule", str(e))
        t = db.create_trigger(
            ctx.org_id, ctx.sub, procedure=inp.procedure, cron=inp.cron, tz=tz,
            next_due=runner_tick.next_due(inp.cron, tz), tools=inp.tools,
            project_id=inp.project_id, input=inp.input, label=inp.label,
            max_steps=inp.max_steps)
        return {"trigger": t}

    if inp.op == "list":
        return {"triggers": db.list_triggers(ctx.org_id)}

    if inp.trigger_id is None:
        raise AuthzDenied(400, "missing_fields", f"{inp.op} exige `trigger_id`")

    if inp.op == "get":
        t = db.get_trigger(inp.trigger_id, ctx.org_id)
        if not t:
            raise AuthzDenied(404, "trigger_not_found", "déclencheur inconnu")
        return {"trigger": t}

    if inp.op == "delete":
        if not db.delete_trigger(inp.trigger_id, ctx.org_id):
            raise AuthzDenied(404, "trigger_not_found", "déclencheur inconnu")
        return {"ok": True}

    # update — partiel ; toute retouche du cadencement (cron OU tz) revalide et
    # recalcule l'échéance avec les valeurs EFFECTIVES (jamais l'une sans l'autre).
    champs: dict[str, Any] = {}
    for c in ("procedure", "tools", "project_id", "input", "label",
              "max_steps", "enabled"):
        v = getattr(inp, c)
        if v is not None:
            champs[c] = v
    if inp.cron is not None or inp.tz is not None:
        actuel = db.get_trigger(inp.trigger_id, ctx.org_id)
        if not actuel:
            raise AuthzDenied(404, "trigger_not_found", "déclencheur inconnu")
        cron = inp.cron if inp.cron is not None else actuel["cron"]
        tz = inp.tz if inp.tz is not None else actuel["tz"]
        try:
            runner_tick.validate_cron(cron, tz)
        except ValueError as e:
            raise AuthzDenied(400, "invalid_schedule", str(e))
        champs.update(cron=cron, tz=tz, next_due=runner_tick.next_due(cron, tz))
    t = db.update_trigger(inp.trigger_id, ctx.org_id, champs)
    if not t:
        raise AuthzDenied(404, "trigger_not_found", "déclencheur inconnu")
    return {"trigger": t}


CAPABILITIES += [
    Capability(
        key="runner.triggers",
        handler=_triggers,
        Input=TriggerInput,
        Output=TriggerOut,
        authz=ORG_MEMBER,
        mcp="oto_trigger",
        rest=RestBinding(verb="POST", path="/api/me/runner/triggers"),
        description=(
            "Scheduled triggers for hosted runs — the product's /schedule. op=create "
            "(procedure slug + `cron` + `tools` allowlist ; `tz` defaults to "
            "Europe/Paris and the cron evaluates IN that timezone — say WHICH 8am "
            "you mean) / list / get / update (editing cron or tz revalidates and "
            "recomputes the next due) / delete. The tick only ENQUEUES a job at "
            "each due time; execution belongs to the worker. Floor between two "
            "occurrences: 5 minutes — a run is not a ping."
        ),
    ),
]
