"""La SANTÉ des agents d'une org — l'onglet Santé d'un tableau de bord (13/11/2026).

Deux routes REST :

- `GET /api/orgs/{id}/agent-health` — membres : les agents qu'ils voient, ceux qui
  fonctionnent, ce qui attend le CLIENT (un problème par cause, avec les agents qu'il
  arrête), ce que le médecin a réparé ces trente derniers jours, et le réglage ;
- `PUT /api/orgs/{id}/agent-health` — admins : « laisser Tulina réparer mes agents »
  (`auto_repair`, allumé par défaut).

Une lecture par ORG, jamais une par agent : l'onglet d'une org de deux cents agents
coûte quelques requêtes. Un problème se dit avec le texte que reçoit Slack
(`alertes_slack_agents.message_du_probleme` : la copie de la cause approuvée, sinon le
texte du médecin, sinon la phrase de la classe) et se regroupe comme lui : les agents
dont le client lirait exactement le même problème forment UN problème.

⚠️ **Deux états** : un agent fonctionne, ou il attend le client. Une panne de NOTRE côté
n'est pas dite au client (la règle du 09/11) : son agent compte parmi ceux qui
fonctionnent, le médecin s'en occupe. Un membre ne voit que les agents qu'il voit dans la
liste des agents (`_acces_agent.niveaux`) ; un admin les voit tous.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from ._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from ._types import Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}

#: Ce qui se dit d'une relance qui a guéri l'agent, par langue.
_RELANCE = {
    "fr": "Il s'est arrêté sur une panne passagère de notre côté. Nous l'avons relancé.",
    "en": "It stopped on a short outage on our side. We ran it again.",
}


class AgentHealthInput(BaseModel):
    org_id: int


class AgentHealthSaveInput(BaseModel):
    org_id: int
    auto_repair: bool


class AgentHealth(BaseModel):
    """`watched` agents allumés que l'appelant voit, dont `working` fonctionnent ;
    `needs_you` = un problème par cause qui attend le client : `{headline, todo, action,
    link, since, agents: [{id, label}]}` ; `fixed` = ce que Tulina a réparé ces trente
    derniers jours, le plus récent d'abord : `{agent: {id, label}, at, what, undo}` ;
    `auto_repair` = le réglage de l'org."""
    watched: int
    working: int
    needs_you: list[dict]
    fixed: list[dict]
    auto_repair: bool


def _visibles(ctx: ResolvedCtx, org_id: int) -> dict[int, dict]:
    from .. import db
    from . import _acces_agent
    agents = db.list_triggers(org_id)
    niveaux = _acces_agent.niveaux(ctx.sub, org_id, agents)
    return {int(t["id"]): t for t in agents if niveaux[int(t["id"])]}


def _nom(t: dict) -> str:
    return (t.get("label") or "").strip() or t.get("procedure") or f"#{t['id']}"


def _quand(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v) if v else None


def _lire(ctx: ResolvedCtx, inp: AgentHealthInput) -> dict:
    from .. import alertes_slack_agents as A
    from ..db import sante_agents
    org_id = int(inp.org_id)
    visibles = _visibles(ctx, org_id)
    allumes = {i: t for i, t in visibles.items() if t.get("enabled")}
    langue = A.langue(org_id)
    marque = A.marque_de_l_org(org_id).nom

    problemes: dict[tuple, dict] = {}
    for ep in sante_agents.episodes_vivants(org_id):
        t = allumes.get(int(ep["trigger_id"]))
        if t is None or ep["cote"] != "proprietaire":
            continue
        pb = A.message_du_probleme(org_id, ep, langue, marque=marque, sub=ctx.sub)
        cle = (pb["headline"], pb.get("todo"), pb.get("link"), pb.get("action"))
        p = problemes.setdefault(cle, {**pb, "since": ep["ouvert_at"], "agents": []})
        p["since"] = min(p["since"], ep["ouvert_at"])
        p["agents"].append({"id": int(t["id"]), "label": _nom(t)})
    arretes = {a["id"] for p in problemes.values() for a in p["agents"]}

    fixed = []
    for ep in sante_agents.reparations_recentes(org_id):
        t = visibles.get(int(ep["trigger_id"]))
        if t is None:
            continue
        note = ep.get("repair_note") or {}
        outil = any((r or {}).get("action") == "add_tool" for r in ep["reparations"] or [])
        fixed.append({"agent": {"id": int(t["id"]), "label": _nom(t)},
                      "at": _quand(ep["clos_at"]),
                      "what": note.get("what") or _RELANCE.get(langue, _RELANCE["en"]),
                      "undo": bool(outil and note.get("what"))})

    needs_you = sorted(problemes.values(), key=lambda p: p["since"])
    for p in needs_you:
        p["since"] = _quand(p["since"])
    return {"watched": len(allumes), "working": len(set(allumes) - arretes),
            "needs_you": needs_you, "fixed": fixed,
            "auto_repair": sante_agents.auto_repair(org_id)}


def _poser(ctx: ResolvedCtx, inp: AgentHealthSaveInput) -> dict:
    from ..db import sante_agents
    sante_agents.poser_auto_repair(int(inp.org_id), inp.auto_repair)
    return _lire(ctx, AgentHealthInput(org_id=inp.org_id))


CAPABILITIES += [
    Capability(key="org.agent_health.get", handler=_lire, Input=AgentHealthInput,
               Output=AgentHealth, authz=ORG_MEMBER_OF("org_id"), mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/agent-health", _ID)),
    Capability(key="org.agent_health.set", handler=_poser, Input=AgentHealthSaveInput,
               Output=AgentHealth, authz=ORG_ADMIN_OF("org_id"), mcp=None,
               rest=RestBinding("PUT", "/api/orgs/{id}/agent-health", _ID)),
]
