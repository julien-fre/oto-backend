"""Capacité « la file d'exécutions du runner » — REST-only, op-aware (chantier R2).

C'est la face que consomme le WORKER externe (`oto-runner`) : enfiler, réclamer,
lier au run ouvert, prolonger le bail, conclure. Pas de face MCP : un agent en
conversation n'a rien à faire dans la plomberie d'exécution — le précédent est la
pose de secrets (dashboard-only) ; ici c'est worker-only, même logique.

**Le scope EST l'org de l'appel** (V1) : un worker porte un jeton d'org et ne voit
que la file de cette org. Le pool multi-org attend l'arbitrage compte-de-service
(ADR 0064 §5-1) — rien ici ne le préjuge, le claim prendra un scope plus large le
jour où l'identité le permettra.

**Un job porte des RÉFÉRENCES, jamais un secret** : la procédure à charger, le
projet, le run à continuer. Le worker résout tout le reste par ses trois contrats
(API de fil, face MCP, clé de modèle) — un payload qui transporterait un credential
serait un coffre parallèle.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import db
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class JobsInput(BaseModel):
    op: Literal["enqueue", "claim", "bind_run", "complete", "extend", "get"]
    # enqueue —
    kind: Optional[Literal["start", "continue"]] = None
    payload: Optional[dict[str, Any]] = None
    run_id: Optional[str] = None
    max_attempts: int = 3
    # claim / extend —
    lease_seconds: int = 600
    # bind_run / complete / extend / get —
    job_id: Optional[int] = None
    ok: Optional[bool] = None
    error: Optional[str] = None


class JobsOut(BaseModel):
    # enqueue → id/status/due_at ; claim → job (ou null, file vide) ; les autres → ok/status.
    id: Optional[int] = None
    status: Optional[str] = None
    due_at: Optional[str] = None
    job: Optional[dict[str, Any]] = None
    ok: Optional[bool] = None


def _jobs(ctx: ResolvedCtx, inp: JobsInput) -> dict:
    if not ctx.org_id:
        raise AuthzDenied(400, "org_required", "la file du runner est org-scopée")

    if inp.op == "enqueue":
        if inp.kind is None:
            raise AuthzDenied(400, "missing_fields", "enqueue exige `kind`")
        if inp.kind == "continue" and not inp.run_id:
            raise AuthzDenied(400, "missing_fields",
                              "un job `continue` exige `run_id` — quel fil reprendre ?")
        if inp.kind == "start" and not inp.payload:
            raise AuthzDenied(400, "missing_fields",
                              "un job `start` exige `payload` (au moins la procédure à charger)")
        res = db.enqueue_job(ctx.org_id, inp.kind, payload=inp.payload,
                             run_id=inp.run_id, max_attempts=inp.max_attempts)
        return {"id": res["id"], "status": res["status"], "due_at": str(res["due_at"])}

    if inp.op == "claim":
        job = db.claim_next_job(ctx.org_id, ctx.sub,
                                lease_seconds=max(30, min(inp.lease_seconds, 3600)))
        return {"job": job}

    # Les quatre verbes de la prise exigent le job — et le db-layer les scope au
    # CLAIMANT : un pair qui tente de conclure le job d'un autre obtient le même
    # refus qu'un job inexistant (rowcount 0 → 404), pas d'oracle.
    if inp.job_id is None:
        raise AuthzDenied(400, "missing_fields", f"{inp.op} exige `job_id`")

    if inp.op == "bind_run":
        if not inp.run_id:
            raise AuthzDenied(400, "missing_fields", "bind_run exige `run_id`")
        if not db.bind_job_run(inp.job_id, ctx.sub, inp.run_id):
            raise AuthzDenied(404, "job_not_found", "job inconnu")
        return {"ok": True}

    if inp.op == "extend":
        if not db.extend_job_lease(inp.job_id, ctx.sub,
                                   lease_seconds=max(30, min(inp.lease_seconds, 3600))):
            raise AuthzDenied(404, "job_not_found", "job inconnu")
        return {"ok": True}

    if inp.op == "complete":
        if inp.ok is None:
            raise AuthzDenied(400, "missing_fields", "complete exige `ok` (true/false)")
        res = db.complete_job(inp.job_id, ctx.sub, inp.ok,
                              error=inp.error, run_id=inp.run_id)
        if res is None:
            # Déjà re-claimé après bail mort, ou jamais à lui : on ne conclut pas
            # ce qui ne nous appartient plus.
            raise AuthzDenied(404, "job_not_found", "job inconnu")
        return {"ok": True, "status": res["status"]}

    # get — lecture org-scopée (diagnostic, dashboard R4)
    job = db.get_job(inp.job_id, ctx.org_id)
    if not job:
        raise AuthzDenied(404, "job_not_found", "job inconnu")
    return {"job": job}


CAPABILITIES += [
    Capability(
        key="runner.jobs",
        handler=_jobs,
        Input=JobsInput,
        Output=JobsOut,
        authz=ORG_MEMBER,
        mcp=None,   # worker-only : la plomberie d'exécution n'a pas de face agent
        rest=RestBinding(verb="POST", path="/api/me/runner/jobs"),
        description=(
            "The runner's execution queue (worker-facing, REST only). op=enqueue "
            "(kind start|continue — a job carries REFERENCES, never a secret) / "
            "claim (atomic, org-scoped, lease — also reclaims expired leases: that "
            "IS the resume) / bind_run / extend (heartbeat) / complete (ok=false "
            "backs off, then marks `failed` VISIBLY at the attempts cap — never "
            "loops) / get. All claim-side verbs are scoped to the claimant."
        ),
    ),
]
