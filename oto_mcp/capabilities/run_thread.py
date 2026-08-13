"""Capacité « le fil d'un run » — append/read, op-aware (chantier runner R1).

Le fil est l'état d'exécution d'un run HÉBERGÉ (ADR 0064 du blueprint) : la suite
des tours — messages et segments provider — que le worker recharge pour continuer,
et que le dashboard donne à lire. Il n'est PAS le journal : la reprise canonique
inter-agents reste le journal du run, et aucune fonction du produit ne doit exiger
le fil (il est purgé court, cf. `db.prune_run_messages`).

**Le fil hérite des droits de son run — aucun modèle de droits nouveau** :
- **append** : le PROPRIÉTAIRE du run seulement (`runs.sub`). C'est le worker qui a
  ouvert le run — ou, plus tard (R4), l'utilisateur qui « continue » SON run.
- **read** : le propriétaire ; un org_admin de l'org du run lit la projection
  NEUTRE seulement — `include_raw` reste au propriétaire, le segment provider
  porte les blocs de thinking du modèle, pas une donnée d'équipe.
- Un run d'une autre org ou d'un autre sub rend le MÊME 404 qu'un run inexistant
  (pas d'oracle d'existence — même règle que `op=call` du monitoring).

**Le tour est borné à l'écriture** (`_MAX_MESSAGE_CHARS`) : un résultat d'outil
géant se tronque à la source (leçon #384) — le fil n'est pas un déversoir, et un
plafond découvert à la lecture serait un plafond découvert trop tard.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import db, roles
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Un tour = projection neutre + segment provider (thinking compris). 256 k chars
# sérialisés absorbent le plus gros tour Anthropic mesuré, et refusent le déversoir
# (un résultat d'outil de 67 k chars N'A PAS sa place ici — il se tronque à la
# source, signal #384).
_MAX_MESSAGE_CHARS = 256_000


class ThreadInput(BaseModel):
    op: Literal["append", "read"]
    run_id: str
    # append —
    role: Optional[Literal["user", "assistant", "tool"]] = None
    content: Optional[dict[str, Any]] = None       # projection NEUTRE du tour
    provider_raw: Optional[dict[str, Any]] = None  # tour provider verbatim (continuation)
    # read —
    after_seq: int = 0
    limit: int = 200
    include_raw: bool = False


class ThreadOut(BaseModel):
    run_id: str
    # append → le rang attribué ; read → les tours.
    seq: Optional[int] = None
    messages: Optional[list[dict[str, Any]]] = None


def _head_or_404(ctx: ResolvedCtx, run_id: str) -> dict:
    head = db.get_run_head(run_id)
    # Autre org, autre sub sans rôle, ou inexistant : indistinguables VOULU.
    if not head:
        raise AuthzDenied(404, "run_not_found", "run inconnu")
    return head


def _thread(ctx: ResolvedCtx, inp: ThreadInput) -> dict:
    head = _head_or_404(ctx, inp.run_id)
    est_proprio = head.get("sub") == ctx.sub
    est_admin_org = bool(head.get("org_id")) and roles.is_org_admin(ctx.sub, head["org_id"])

    if inp.op == "append":
        if not est_proprio:
            # L'org_admin LIT, il n'écrit pas dans le fil d'autrui : un fil à deux
            # plumes ne serait plus l'état d'exécution de personne.
            raise AuthzDenied(404, "run_not_found", "run inconnu")
        if inp.role is None or inp.content is None:
            raise AuthzDenied(400, "missing_fields", "append exige `role` et `content`")
        taille = len(json.dumps(inp.content, ensure_ascii=False)) + (
            len(json.dumps(inp.provider_raw, ensure_ascii=False)) if inp.provider_raw else 0)
        if taille > _MAX_MESSAGE_CHARS:
            raise AuthzDenied(
                400, "message_too_large",
                f"tour de {taille} caractères pour un plafond de {_MAX_MESSAGE_CHARS} — "
                "tronque le résultat d'outil À LA SOURCE, le fil n'est pas un déversoir")
        res = db.append_run_message(inp.run_id, inp.role, inp.content, inp.provider_raw)
        return {"run_id": inp.run_id, "seq": res["seq"]}

    # read —
    if not (est_proprio or est_admin_org):
        raise AuthzDenied(404, "run_not_found", "run inconnu")
    if inp.include_raw and not est_proprio:
        raise AuthzDenied(
            403, "raw_is_owner_only",
            "le segment provider est réservé au propriétaire du run — "
            "la projection neutre porte tout ce qui se lit")
    messages = db.get_run_messages(inp.run_id, after_seq=inp.after_seq,
                                   limit=inp.limit, include_raw=inp.include_raw)
    return {"run_id": inp.run_id, "messages": messages}


CAPABILITIES += [
    Capability(
        key="runs.thread",
        handler=_thread,
        Input=ThreadInput,
        Output=ThreadOut,
        authz=ORG_MEMBER,
        mcp="oto_run_thread",
        rest=RestBinding(verb="POST", path="/api/me/runs/thread"),
        description=(
            "The THREAD of a hosted run — its execution state, not its journal. "
            "op=append (owner only: role + neutral `content`, optional verbatim "
            "`provider_raw` for faithful continuation) / op=read (owner, or an org "
            "admin of the run's org — neutral projection only; `include_raw` stays "
            "with the owner). The thread is short-lived by design (pruned at boot): "
            "nothing in the product may REQUIRE it — cross-agent resume reads the "
            "run's journal instead. Turns are size-capped at write time: truncate "
            "huge tool results at the source, the thread is not a spillway."
        ),
    ),
]
