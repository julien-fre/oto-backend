"""Capacité « déclencheurs du runner » — la config qui fabrique des jobs (R3).

Deux faces, et c'est un choix de principe : un déclencheur est de la CONFIG
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
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx,
                     RestBinding)
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


class Trigger(BaseModel):
    """Un déclencheur tel que servi (les colonnes de `_COLS`, db/runner_triggers) :
    la procédure à jouer, quand (cron + tz), avec quels outils, et l'état de
    marche (enabled, next_due, last_enqueued_at)."""
    id: int
    org_id: Optional[int] = None
    sub: Optional[str] = None
    label: Optional[str] = None
    procedure: Optional[str] = None
    project_id: Optional[int] = None
    tools: Optional[list[str]] = None
    input: Optional[str] = None
    max_steps: Optional[int] = None
    cron: Optional[str] = None
    tz: Optional[str] = None
    enabled: Optional[bool] = None
    next_due: Optional[str] = None
    last_enqueued_at: Optional[str] = None
    created_at: Optional[str] = None
    #: Ce que ce déclencheur a PERDU : des occurrences enfilées que personne n'est
    #: venu prendre dans leur cycle, et que le tick a périmées.
    #: ⚠️ Servi avec le déclencheur parce que c'est là qu'on le cherche. Compté à
    #: la main dans la file, il n'était visible de personne : quarante-et-une
    #: occurrences perdues sur treize jours n'ont été découvertes que le 02/09,
    #: en préparant autre chose.
    #: `0` est un vrai zéro (rien n'a été perdu), pas une absence de mesure.
    expired_count: Optional[int] = None
    #: La PREMIÈRE occurrence perdue et la DERNIÈRE : « depuis quand » et « est-ce
    #: encore en cours » sont deux questions différentes, et une seule date les
    #: confondrait. Une perte ancienne qui a cessé n'appelle pas le même geste
    #: qu'une perte qui continue ce matin.
    expired_since: Optional[str] = None
    expired_last: Optional[str] = None


class RunnerArme(BaseModel):
    """La présence d'un runner pour l'org — SERVIE avec les déclencheurs.

    ⚠️ Elle accompagne `list`/`get` parce qu'un déclencheur ne porte pas en
    lui-même de quoi savoir s'il sera joué : c'est une propriété de l'ORG, et
    elle manquait exactement là où on la cherche. Sans elle, la seule trace
    qu'un déclencheur ne s'exécute pas a été, le 26/08, une phrase tapée dans
    son propre LIBELLÉ."""
    armed: bool
    workers: int
    #: `None` = aucun worker n'est JAMAIS venu ; une date = il s'est tu depuis.
    #: Les deux n'appellent pas le même geste, et un seul booléen les confondrait.
    last_seen: Optional[str] = None


class TriggerOut(BaseModel):
    trigger: Optional[Trigger] = None
    triggers: Optional[list[Trigger]] = None
    ok: Optional[bool] = None
    runner: Optional[RunnerArme] = None


def _avec_pertes(org_id: int, t: dict) -> dict:
    """Le déclencheur, augmenté de ce qu'il a perdu.

    ⚠️ Un déclencheur ne porte pas en lui-même la trace de ses occurrences
    perdues — elles vivent dans la file, que personne ne lit. **Une perte que
    seule une requête manuelle révèle n'est pas une perte connue** : les
    quarante-et-une occurrences de treize jours ont été découvertes par hasard,
    en préparant autre chose. Servi ici, l'écart se voit là où on le cherche.
    """
    return {**t, **db.comptage_perime(org_id, t["id"])}


def _exige_un_runner(org_id: int) -> None:
    """Refuse de PROMETTRE une exécution que personne n'assure.

    ⚠️ La garde suit le VERBE, pas l'objet — le motif que `runner_fleets` a
    établi pour `launch`/`stop`. Poser un déclencheur (ou en rallumer un) est le
    geste qui MENT : il rend un `next_due`, que l'agent rapporte comme une
    promesse tenue. Lire, corriger et supprimer restent ouverts, précisément
    parce que c'est ce dont a besoin quelqu'un qui découvre un déclencheur mort.

    Fermer `create` derrière la présence d'un worker n'ôte donc rien à personne :
    ce qui existe reste gérable, et ce qui n'aurait jamais tourné ne se crée
    plus en silence."""
    etat = db.runner_arme(org_id)
    if etat["armed"]:
        return
    if etat["last_seen"] is None:
        detail = ("aucun worker n'a jamais sondé la file de cette org : rien "
                  "n'exécuterait ce déclencheur")
    else:
        detail = (f"le dernier worker de cette org s'est tu le "
                  f"{etat['last_seen']} — au-delà de "
                  f"{db.ARME_FENETRE_S // 60} minutes on ne le tient plus pour "
                  f"présent")
    raise AuthzDenied(
        400, "no_runner_armed",
        f"aucun runner armé pour cette org ({detail}). Le tick ENFILE un job à "
        "chaque échéance ; l'exécution appartient au worker, et sans worker le "
        "job resterait `pending` pour toujours, sans erreur — le déclencheur "
        "aurait l'air de marcher. Arme un worker pour cette org "
        "(`OTO_RUNNER_ARMED=1` + un jeton de l'org, cf. otomata-tech/oto-runner), "
        "puis repose le déclencheur. Lecture, modification et suppression des "
        "déclencheurs existants restent ouvertes.")


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
        # Après la validation du cadencement, avant l'écriture : un cron fautif
        # se corrige, une org sans runner appelle un autre geste — les deux
        # refus ne se remplacent pas, et celui qu'on lit d'abord est celui qu'on
        # peut réparer sans quitter l'appel.
        _exige_un_runner(ctx.org_id)
        t = db.create_trigger(
            ctx.org_id, ctx.sub, procedure=inp.procedure, cron=inp.cron, tz=tz,
            next_due=runner_tick.next_due(inp.cron, tz), tools=inp.tools,
            project_id=inp.project_id, input=inp.input, label=inp.label,
            max_steps=inp.max_steps)
        return {"trigger": t}

    if inp.op == "list":
        return {"triggers": [_avec_pertes(ctx.org_id, t)
                             for t in db.list_triggers(ctx.org_id)],
                "runner": db.runner_arme(ctx.org_id)}

    if inp.trigger_id is None:
        raise AuthzDenied(400, "missing_fields", f"{inp.op} exige `trigger_id`")

    if inp.op == "get":
        t = db.get_trigger(inp.trigger_id, ctx.org_id)
        if not t:
            raise AuthzDenied(404, "trigger_not_found", "déclencheur inconnu")
        return {"trigger": _avec_pertes(ctx.org_id, t),
                "runner": db.runner_arme(ctx.org_id)}

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
    actuel = None
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
    # Rallumer, c'est promettre à nouveau : même geste, même garde. Éteindre,
    # renommer ou corriger un cron ne promet rien et passe toujours — sinon un
    # déclencheur mort deviendrait impossible à ranger.
    if champs.get("enabled") is True:
        _exige_un_runner(ctx.org_id)
        # ⚠️ **RALLUMER REPREND LE RYTHME, ça ne rembobine pas** (arbitré le
        # 02/09, #826). Une échéance figée pendant l'extinction est restée dans
        # le PASSÉ : sans ce recalcul, le tick voyait le déclencheur dû à la
        # seconde du rallumage et enfilait aussitôt — une exécution que personne
        # n'a demandée, déclenchée par le geste de quelqu'un qui répare.
        #
        # ⚠️ Et la cohérence l'impose, pas seulement le confort : éteindre PÉRIME
        # les occurrences en attente. *Un système qui dit « ce qui a attendu
        # pendant l'extinction est mort » ne peut pas dire « sauf l'échéance ».*
        # Une échéance manquée pendant une extinction VOULUE n'a pas été manquée.
        #
        # ⚠️ Seul le PASSAGE à allumé recalcule — même motif que la péremption,
        # qui ne mord qu'au passage à éteint. Recalculer sur un déclencheur déjà
        # allumé donnerait un moyen de repousser son échéance indéfiniment, en
        # répétant un geste qui n'est pas censé rien changer.
        #
        # ⚠️ Lu APRÈS `_exige_un_runner`, jamais avant : l'ordre des refus est un
        # contrat. Lire le déclencheur d'abord ferait répondre « inconnu » (404)
        # là où le serveur répond aujourd'hui « aucun runner » — deux diagnostics
        # opposés pour la même org, et celui qu'on retirerait est le seul qui dit
        # quoi faire.
        if actuel is None:
            actuel = db.get_trigger(inp.trigger_id, ctx.org_id)
        if actuel and not actuel["enabled"] and "next_due" not in champs:
            champs["next_due"] = runner_tick.next_due(actuel["cron"], actuel["tz"])
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
        # Les refus PUBLIÉS — un dashboard doit pouvoir GRISER « nouveau
        # déclencheur » et dire pourquoi, plutôt que laisser tenter un geste qui
        # sera refusé (le motif de `runner_fleets`).
        errors=(
            DeclaredError(400, "missing_fields",
                          "`create` sans `procedure`/`cron`/`tools`, ou une "
                          "opération sur un déclencheur sans `trigger_id`"),
            DeclaredError(400, "invalid_schedule",
                          "cron malformé, fuseau inconnu, ou deux occurrences "
                          "espacées de moins de 5 minutes"),
            DeclaredError(400, "no_runner_armed",
                          "aucun worker ne sonde la file de cette org : "
                          "`create`, et `update enabled=true`, sont refusés "
                          "plutôt que de promettre une exécution qui n'aurait "
                          "pas lieu"),
            DeclaredError(404, "trigger_not_found",
                          "déclencheur inconnu dans l'org du porteur"),
        ),
        rest=RestBinding(verb="POST", path="/api/me/runner/triggers"),
        description=(
            "Scheduled triggers for hosted runs — the product's /schedule. op=create "
            "(procedure slug + `cron` + `tools` allowlist ; `tz` defaults to "
            "Europe/Paris and the cron evaluates IN that timezone — say WHICH 8am "
            "you mean) / list / get / update (editing cron or tz revalidates and "
            "recomputes the next due) / delete. The tick only ENQUEUES a job at "
            "each due time; execution belongs to the worker. Floor between two "
            "occurrences: 5 minutes — a run is not a ping. `create` (and "
            "`update enabled=true`) is REFUSED when no worker polls this org's "
            "queue — a trigger nothing executes would enqueue forever without an "
            "error; `list`/`get` carry `runner` (armed, workers, last_seen) so an "
            "existing trigger can be told apart from a live one. ⚠️ An occurrence "
            "nobody claimed BEFORE the next one is due is EXPIRED, not silently "
            "kept: a daily watch run thirteen days late does not return a late "
            "result, it returns a WRONG one — and a backlog released all at once "
            "would run with the procedure and context of its era. Expiry never "
            "deletes: `list`/`get` carry `expired_count` (a real 0, not a missing "
            "measure) plus `expired_since` and `expired_last` — since when, and "
            "whether it is STILL happening, are two different questions. A rising "
            "count on an enabled trigger means nobody is executing this org."
        ),
    ),
]
