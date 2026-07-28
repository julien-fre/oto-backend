"""Note personnelle de l'utilisateur — routes HISTORIQUES `/api/me/agent-readme`.

⚠️ **En sursis** (ADR 0042 §Convergence des surfaces, barreau 3) : cette note n'est
qu'un **guide** `scope='user', delivery='init'` — le dernier survivant du vocabulaire
d'avant (« agent readme »). La surface canonique est désormais
`GET`/`PUT /api/me/guides/user/readme?delivery=init` (capacité `me.guides.*`).

Ce module ne porte donc PLUS d'implémentation : il **délègue** aux handlers de
`guides.py`, le temps que le dashboard bascule. À supprimer aussitôt après (le seul
consommateur est `AgentReadmeCard`).
"""
from __future__ import annotations

from pydantic import BaseModel

from ._authz import SUB_ONLY
from ._types import Capability, ResolvedCtx, RestBinding
from .guides import GuideRefInput, GuideSetInput, _get, _set
from .registry import CAPABILITIES


class _NoInput(BaseModel):
    pass


class SetReadmeInput(BaseModel):
    body_md: str = ""


def _readme_ref(**kw) -> dict:
    return {"scope": "user", "delivery": "init", **kw}


def _get_readme(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    g = _get(ctx, GuideRefInput(**_readme_ref()))
    return {"body_md": g["body_md"], "updated_at": g["updated_at"]}


def _set_readme(ctx: ResolvedCtx, inp: SetReadmeInput) -> dict:
    g = _set(ctx, GuideSetInput(**_readme_ref(body_md=inp.body_md)))
    return {"body_md": g["body_md"], "updated_at": g["updated_at"]}


CAPABILITIES += [
    Capability(
        key="me.agent_readme.get", handler=_get_readme, Input=_NoInput,
        authz=SUB_ONLY,
        description="DEPRECATED alias of guide (scope=user, delivery=init) — the user's "
                    "personal readme, injected into every session after org and team.",
        rest=RestBinding("GET", "/api/me/agent-readme"),
    ),
    Capability(
        key="me.agent_readme.set", handler=_set_readme, Input=SetReadmeInput,
        authz=SUB_ONLY,
        description="DEPRECATED alias of guide (scope=user, delivery=init). "
                    "`body_md` empty clears the layer.",
        rest=RestBinding("PUT", "/api/me/agent-readme"),
    ),
]
