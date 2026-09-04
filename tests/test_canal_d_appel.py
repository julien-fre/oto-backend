"""D'où vient l'appel — et pourquoi ça se lit sur le MONTAGE, jamais ailleurs.

`ResolvedCtx.channel` porte `"mcp"` quand un agent parle, `"rest"` sur la face HTTP.
Il sert un cran d'intention décidé le 04/09/2026 : rendre un contenu lisible sans
login ne doit pas pouvoir sortir d'une conversation. Le chemin qui l'a fait naître est
celui d'un agent qui publie ce qu'on ne lui a jamais demandé de publier.

⚠️ **Ce banc existe parce que la garde est inerte si le canal n'est pas posé.** Les
gardes ne refusent que sur `"mcp"` EXPLICITE — un adaptateur qui oublierait de le
poser rendrait toute la suite VERTE en n'empêchant plus rien. C'est le pire des cas :
un contrôle qui ment sans le savoir. On ne teste donc pas `dataclasses.replace`, on
fait passer un appel par l'adaptateur réel et on regarde ce que le HANDLER reçoit.
"""
from __future__ import annotations

import asyncio

from fastmcp import Client, FastMCP
from pydantic import BaseModel

from oto_mcp.capabilities import _mcp_adapter
from oto_mcp.capabilities._types import Capability, ResolvedCtx


class _SondeInput(BaseModel):
    op: str = "get"


_VU: dict = {}


def _sonde(ctx: ResolvedCtx, inp: _SondeInput) -> dict:
    _VU["channel"] = ctx.channel
    return {"ok": True}


def _cap() -> Capability:
    return Capability(
        key="sonde.canal", handler=_sonde, Input=_SondeInput,
        authz=lambda raw, inp: ResolvedCtx(sub="u1", org_id=7),
        mcp="sonde_canal",
    )


def test_un_appel_MCP_arrive_au_handler_marque_mcp():
    """LE banc qui compte. Si l'adaptateur cessait de poser le canal, ce test-ci
    tomberait — et c'est le seul endroit où cette panne-là devient visible : toutes
    les gardes qui s'appuient dessus resteraient vertes en ne gardant plus rien."""
    _VU.clear()
    mcp = FastMCP("sonde")
    _mcp_adapter.register(mcp, [_cap()])

    async def _appeler():
        async with Client(mcp) as c:
            await c.call_tool("sonde_canal", {"op": "get"})

    asyncio.run(_appeler())
    assert _VU["channel"] == "mcp", (
        "le handler n'a pas su qu'un AGENT l'appelait : toute garde « public interdit "
        "à l'agent » est alors inerte, et verte.")


def test_le_canal_n_est_pas_pose_par_la_regle_d_autz():
    """La règle sert les DEUX faces : si elle posait le canal, elle mentirait sur la
    moitié des appels. Le contexte qu'elle rend seule n'a donc pas de canal —
    c'est le seuil qui le pose, et lui seul."""
    ctx = _cap().authz(None, _SondeInput())
    assert ctx.channel is None


def test_un_contexte_sans_canal_ne_se_lit_PAS_comme_humain():
    """Le défaut est `None`, pas `"rest"`. La nuance décide du sens des gardes : elles
    refusent sur `"mcp"` explicite, jamais sur « pas rest ». Un défaut `"rest"` ferait
    passer tout appel interne — et tout adaptateur muet — pour un geste humain."""
    assert ResolvedCtx(sub="u1").channel is None
