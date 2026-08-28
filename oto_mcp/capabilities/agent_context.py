"""Capacité « contexte agent » (issue otomata-private#49) — vue de transparence.

`GET /api/me/agent-context` rend, pour l'utilisateur courant, **exactement ce que
son Claude reçoit d'oto** au handshake, assemblé en 3 couches dérivées (zéro état
neuf) :

1. **instructions** — les instructions serveur statiques (`instructions.render()` :
   posture + bootstrap + boucle d'usage + catalogue de namespaces dérivé du registre).
2. **doctrine** — la doctrine d'org effective (bundle session-start), via le handler
   canonique `orgs_instructions._get_doctrine` (réemploi, pas de duplication).
3. **tools** — les outils EFFECTIVEMENT visibles pour `(sub, org active)`, via la
   logique de visibilité canonique `session_visibility.compute_hidden_tools` (même
   calcul que le handshake MCP), regroupés par namespace.

REST-only : l'agent n'a pas besoin de s'appeler lui-même (il a déjà ce contexte) ;
la surface sert le dashboard. `SUB_ONLY` → chacun voit le sien.
"""
from __future__ import annotations

import logging
import types

from typing import Optional

from pydantic import BaseModel

from .. import instructions as _instructions
from .. import session_visibility, tool_registry
from .orgs import instructions as orgs_instructions
from ._authz import SUB_ONLY
from ._types import Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


class AgentContextInput(BaseModel):
    pass


def _namespace_of(tool_name: str) -> str:
    return tool_name.split("_", 1)[0]


class ContextLayer(BaseModel):
    """Une couche de l'artefact injecté, dans son ORDRE d'injection. `chars` est son
    poids : c'est le thermomètre de 0055-D5 — on mesure et on alerte, on n'ampute
    jamais. Les couches au corps vide sont omises."""
    key: str                                     # platform | catalog | … (bloc C)
    label: str
    body: str
    chars: int


class ToolsNamespace(BaseModel):
    namespace: str
    visible: int
    total: int


class ToolsView(BaseModel):
    """⚠️ `available: false` signifie que la vue n'a **pas pu être dérivée** (hors
    serveur, ou échec du calcul de visibilité) — ce n'est **pas** « aucun outil ».
    Un front qui l'affiche comme un zéro ment ; les compteurs sont alors absents."""
    available: bool
    total_visible: Optional[int] = None
    total_hidden: Optional[int] = None
    namespaces: Optional[list[ToolsNamespace]] = None


class AgentContextView(BaseModel):
    """Ce que le Claude de cet utilisateur reçoit vraiment — la vue de transparence."""
    org_id: Optional[int] = None
    # L'artefact EXACT injecté au handshake (blocs A + C concaténés).
    instructions: str
    # Le MÊME artefact, décomposé. Invariant tenu par `instructions.session_layers` :
    # `"\n\n".join(couches non vides) == instructions`.
    layers: list[ContextLayer]
    # La doctrine d'org résolue, telle que la sert `oto_get_doctrine` — forme non
    # redéclarée ici pour ne pas en tenir deux copies.
    doctrine: dict
    tools: ToolsView


async def _tools_view(ctx: ResolvedCtx) -> dict:
    """Outils visibles/masqués pour `(sub, org active)`, groupés par namespace.
    Réutilise `compute_hidden_tools` (logique de visibilité du handshake) via un
    shim portant l'instance FastMCP liée au boot."""
    inst = tool_registry.bound_instance()
    if inst is None:
        return {"available": False}   # hors serveur (tests) — pas d'instance liée
    try:
        all_tools = await inst.list_tools(run_middleware=False)
        # Scope EXPLICITE sur l'org de la vue (ctx.org_id) — sinon compute_hidden_tools
        # re-dérive current_org(sub), qui dans ce chemin REST retombe sur la sélection
        # GLOBALE (org 0) et gonfle le compte à ~609 sur une org neuve vide (oto/#5.3).
        hidden = await session_visibility.compute_hidden_tools(
            types.SimpleNamespace(fastmcp=inst), ctx.sub, org=ctx.org_id)
    except Exception as e:           # derive-only : on n'échoue pas la vue
        logger.warning("agent-context tools view failed for %s: %s", ctx.sub, e)
        return {"available": False}
    by_ns: dict[str, dict] = {}
    for t in all_tools:
        ns = _namespace_of(t.name)
        slot = by_ns.setdefault(ns, {"namespace": ns, "visible": 0, "total": 0})
        slot["total"] += 1
        if t.name not in hidden:
            slot["visible"] += 1
    namespaces = sorted(by_ns.values(), key=lambda s: s["namespace"])
    total_visible = sum(s["visible"] for s in namespaces)
    return {
        "available": True,
        "total_visible": total_visible,
        "total_hidden": len(all_tools) - total_visible,
        "namespaces": namespaces,
    }


async def _agent_context(ctx: ResolvedCtx, inp: AgentContextInput) -> dict:
    doctrine = await orgs_instructions._get_doctrine(
        ctx, types.SimpleNamespace(slug=None, scope=None, version=None,
                                   org_id=None, with_history=False))
    # Instructions RÉELLEMENT reçues = artefact composé A/C (#50), même chemin que
    # DynamicInstructionsMiddleware → la vue montre exactement ce que reçoit l'agent.
    # `layers` = le même artefact DÉCOMPOSÉ (pile de couches avec poids) pour la vue
    # anatomie de /context ; `instructions` (concaténé) reste pour compat.
    layers = _instructions.session_layers(ctx.sub, ctx.org_id)
    return {
        "org_id": ctx.org_id,
        "instructions": "\n\n".join(l["body"] for l in layers if l["body"]),
        "layers": [{**l, "chars": len(l["body"])} for l in layers if l["body"]],
        "doctrine": doctrine,
        "tools": await _tools_view(ctx),
    }


CAPABILITIES += [
    Capability(
        key="me.agent_context", handler=_agent_context, Input=AgentContextInput,
        authz=SUB_ONLY, Output=AgentContextView,
        description="The exact oto context this user's Claude receives: static server "
                    "instructions (posture + derived namespace catalog), effective org "
                    "doctrine, and the tools currently visible for the active org.",
        rest=RestBinding("GET", "/api/me/agent-context"),
    ),
]


# ── oto_context : rechargement PULL du contexte au changement de scope (call pt 1) ──
# Les instructions injectées sont FIGÉES au handshake (MCP n'a pas de « instructions
# changed »). Quand l'agent bascule d'org/équipe/projet en cours de session, la toolbox
# (tools/list_changed) et les credentials (résolution par appel, ADR 0038) suivent, mais
# le bloc C (readme org+équipe, guides, procédures) reste gelé. `oto_context` laisse
# l'agent TIRER ce bloc C frais pour le scope EFFECTIF — `ctx.org_id` respecte les jetons
# org=/project=/group= de l'appel. Focalisé (bloc C seul, pas la doctrine ni les tools) :
# c'est ce qui change au switch (leçon D1 : ne pas gonfler la sortie).
class ContextInput(BaseModel):
    pass


def _context(ctx: ResolvedCtx, inp: ContextInput) -> dict:
    return {"org_id": ctx.org_id, "context": _instructions._block_c(ctx.sub, ctx.org_id)}


CAPABILITIES += [
    Capability(
        key="me.context", handler=_context, Input=ContextInput, authz=SUB_ONLY,
        # ⚠️ **La 1re ligne est un CONTRAT DE SÉLECTION** : c'est tout ce que certains
        # clients montrent au modèle au moment de choisir un outil. Phrase complète,
        # courte, autonome, impérative — le reste est du détail pour l'appel.
        #
        # Elle a dit « Reload YOUR contextual instructions » jusqu'au 2026-08-28, ce qui
        # se lisait comme un rechargement OPTIONNEL après coup, et annonçait un contexte
        # « frozen at connection time » — c'est-à-dire un mécanisme qu'on croyait fiable.
        # Il ne l'est pas : le champ `instructions` du handshake est tronqué à 2048
        # caractères par Claude Code et n'atteint pas le modèle sur claude.ai
        # (oto-backend#478). Cet outil est donc, en pratique, le SEUL canal sûr — d'où
        # l'impératif. Une description d'outil, elle, arrive toujours.
        description="CALL THIS FIRST, before any other oto tool — it carries your org's "
                    "working rules and guardrails.\n"
                    "Returns the contextual instructions for your CURRENT effective scope: "
                    "org + team agent-readme, guides index, procedures index, recent "
                    "projects/runs.\n"
                    "⚠️ The same context is also injected at connection time, but do NOT "
                    "assume you received it: several clients truncate it, and some drop it "
                    "entirely. If you have not read it in this session, you are missing "
                    "rules your org expects you to follow — including when an action needs "
                    "human validation before it goes out.\n"
                    "Call it again after you switch org/team/project — or pass "
                    "org=/project=/group= here: the toolbox and credentials follow the "
                    "switch, this prose does not.",
        mcp="oto_context",
    ),
]
