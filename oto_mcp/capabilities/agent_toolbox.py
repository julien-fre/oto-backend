"""La boîte à outils de l'agent, telle que la poignée de main la calcule (ADR 0050 §E10).

`GET /api/me/agent-toolbox` rend, pour `(sub, org consultée)`, **exactement** ce que
l'agent de cet utilisateur voit à l'ouverture d'une conversation : la liste des outils
visibles, regroupée par connecteur — et, à côté, les connecteurs INSTALLÉS dont aucun
outil n'est visible, avec la raison (en pause, coupé, restreint).

Pourquoi une route : les écrans refaisaient le calcul de leur côté, et le faisaient
faux (oto#166, constat du 11/09/2026) — l'accueil comptait « actifs » des connecteurs
non installés (une clé résolvable suffisait), la page contexte montrait tout le
catalogue (`/api/me/tools` ignore la sélection). Ici il n'y a **aucun recalcul** : la
liste sort de `session_visibility.compute_hidden_tools`, la fonction du handshake, sur
l'instance FastMCP liée au boot — la même que `me.agent_context`, au grain connecteur.

REST-only : l'agent n'a pas besoin de se demander ce qu'il voit (il le voit) ; la
surface sert le dashboard et le front du tenant partenaire. `SUB_ONLY` → chacun la
sienne ; l'org consultée vient de `X-Oto-Org` (seam ADR 0023), comme `me.agent_context`.

⚠️ `available: false` = la vue n'a pas pu être dérivée (hors serveur, ou échec du calcul)
— jamais « aucun outil ». Un compteur qui l'affiche comme un zéro ment.
"""
from __future__ import annotations

import logging
import types
from typing import Literal, Optional

from pydantic import BaseModel

from .. import providers, session_visibility, tool_registry
from ..connectors import activation as connector_activation
from ..connectors import selection as connector_selection
from ..tool_visibility import namespace_of
from ._authz import SUB_ONLY
from ._types import Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


class AgentToolboxInput(BaseModel):
    pass


class SeenConnector(BaseModel):
    """Un connecteur dont l'agent voit au moins un outil."""
    name: str
    label: str
    tools: int                                   # outils visibles de ce connecteur
    origin: Optional[str] = None                 # provenance de l'installation (§E7)


class InstalledNotSeen(BaseModel):
    """Un connecteur INSTALLÉ dont l'agent ne voit aucun outil, et pourquoi.

    `paused` = le membre l'a mis en pause ; `cut` = l'org (ou la plateforme) l'a coupé —
    il revient seul à la réouverture ; `no_tools` = installé mais aucun outil monté
    sous ce nom (module non chargé)."""
    name: str
    label: str
    state: Literal["active", "paused"]
    origin: Optional[str] = None
    reason: Literal["paused", "cut", "no_tools"]


class AgentToolbox(BaseModel):
    org_id: Optional[int] = None
    available: bool
    # Les NOMS d'outils visibles, triés — l'ensemble exact de la poignée de main.
    tools: Optional[list[str]] = None
    tools_total: Optional[int] = None            # outils montés sur l'instance, visibles ou non
    spine_tools: Optional[int] = None            # visibles sans connecteur (oto_*, data_*, …)
    connectors: Optional[list[SeenConnector]] = None
    installed_not_seen: Optional[list[InstalledNotSeen]] = None


def _reason(name: str, state: str, exposed: set) -> str:
    if state == connector_selection.PAUSED:
        return "paused"
    if name not in exposed:
        return "cut"
    return "no_tools"


async def _toolbox(ctx: ResolvedCtx, inp: AgentToolboxInput) -> dict:
    inst = tool_registry.bound_instance()
    if inst is None:
        return {"org_id": ctx.org_id, "available": False}
    try:
        all_tools = await inst.list_tools(run_middleware=False)
        hidden = await session_visibility.compute_hidden_tools(
            types.SimpleNamespace(fastmcp=inst), ctx.sub, org=ctx.org_id)
    except Exception as e:           # derive-only : la vue dit qu'elle n'a pas pu
        logger.warning("agent-toolbox indisponible pour %s: %s", ctx.sub, e)
        return {"org_id": ctx.org_id, "available": False}
    names = {t.name for t in all_tools}
    visible = sorted(names - hidden)
    org = ctx.org_id or 0
    detail = connector_selection.list_selection_detail(ctx.sub, org)
    par_connecteur: dict[str, int] = {}
    spine = 0
    for n in visible:
        c = providers.connector_for_namespace(namespace_of(n))
        if c is None:
            spine += 1
        else:
            par_connecteur[c.name] = par_connecteur.get(c.name, 0) + 1
    seen = [{"name": name, "label": providers.REGISTRY[name].label, "tools": k,
             "origin": (detail.get(name) or {}).get("origin")}
            for name, k in sorted(par_connecteur.items())]
    not_seen = []
    if any(name not in par_connecteur for name in detail):
        exposed = connector_activation.exposed_connectors(ctx.org_id)
        for name, d in sorted(detail.items()):
            if name in par_connecteur or name not in providers.REGISTRY:
                continue
            not_seen.append({"name": name, "label": providers.REGISTRY[name].label,
                             "state": d["state"], "origin": d["origin"],
                             "reason": _reason(name, d["state"], exposed)})
    return {"org_id": ctx.org_id, "available": True, "tools": visible,
            "tools_total": len(names), "spine_tools": spine,
            "connectors": seen, "installed_not_seen": not_seen}


CAPABILITIES += [
    Capability(
        key="me.agent_toolbox", handler=_toolbox, Input=AgentToolboxInput,
        authz=SUB_ONLY, Output=AgentToolbox, mcp=None,
        description="What this user's agent actually sees at the start of a conversation, "
                    "for the consulted org (X-Oto-Org): the exact list of visible tools, "
                    "grouped by connector, plus the installed connectors whose tools are "
                    "hidden and why (paused / cut / no_tools). Computed by the "
                    "handshake's own visibility function — read this instead of recomputing "
                    "'active connectors' client-side. available:false = could not be derived, "
                    "never 'no tools'.",
        rest=RestBinding("GET", "/api/me/agent-toolbox"),
    ),
]
