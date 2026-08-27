"""Capacités « signaux d'usage » (ADR 0017, barreau 3) : un seul signal volontaire
`feedback` — retour sur un outil (`signal='tool_feedback'`) OU remontée d'un cas
d'usage non couvert (`signal='gap'`). Même substrat, axe explicite.

Co-déclaré MCP + REST (ADR 0009) → émis par les **agents** (tool `feedback`,
auto-journalisé dans tool_calls + corrélé run_id) ET par des **humains** (dashboard,
POST REST). Le contenu durable atterrit dans `usage_signals` (hors prune). Le `gap`
fait de l'agent un capteur de demande non satisfaite.

Handler SYNC (les adaptateurs n'awaitent pas) : on capte `session_id` (propriété
sync du contexte) ; le `run_id` du face-agent vit déjà dans la row tool_calls jumelle.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from .. import db, org_store
from ._authz import PLATFORM_ADMIN, SUB_ONLY
from ._types import AuthzDenied, cap_limit, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class FeedbackInput(BaseModel):
    signal: Literal["tool_feedback", "gap"]
    # tool_feedback: bug | misleading_doc | wrong_result | praise | other
    # gap:           missing_tool | missing_doctrine | missing_data | other
    kind: str
    target: str           # tool_feedback: nom de l'outil ; gap: ce que tu voulais faire
    text: Optional[str] = None


class SignalRecorded(BaseModel):
    """Accusé d'enregistrement d'un signal d'usage — **pas un accusé de traitement**.
    `ok: true` dit que la remontée est écrite et sera vue ; il ne promet ni réponse,
    ni correction, ni notification en retour. Le suivi vit ailleurs (les projections
    admin `usage.signals`, où le signal se résout).

    Rien n'est dédupliqué : deux appels identiques donnent deux lignes et deux `id`
    distincts. C'est volontaire — la répétition d'un même manque EST le signal (ADR
    0017) —, mais un client qui rejoue sa requête sur timeout double son propre poids.

    L'échec, lui, n'a pas de forme : l'écriture est synchrone, une panne remonte en
    erreur HTTP et non en `ok: false`. Il n'existe donc pas de 200 négative ici."""
    ok: bool                     # toujours `true` — un échec ne prend pas ce chemin
    # Identifiant durable de la ligne `usage_signals`, à citer pour la résoudre côté
    # plateforme. Croît strictement, tous signaux et tous émetteurs confondus.
    id: int


def _correlation() -> tuple[str, Optional[str]]:
    """(source, session_id). Contexte MCP présent → 'agent' + session ; sinon
    (REST humain) → 'human' + None. Best-effort, jamais bloquant."""
    try:
        from fastmcp.server.dependencies import get_context
        ctx = get_context()
        return "agent", ctx.session_id
    except Exception:
        return "human", None


def _active_org(sub: str) -> Optional[int]:
    try:
        return org_store.get_active_org(sub)
    except Exception:
        return None


def _feedback(ctx: ResolvedCtx, inp: FeedbackInput) -> dict:
    source, session_id = _correlation()
    sid = db.insert_usage_signal(
        sub=ctx.sub, org_id=ctx.org_id or _active_org(ctx.sub),
        signal=inp.signal, kind=inp.kind, target=inp.target, body=inp.text,
        session_id=session_id, source=source,
    )
    return {"ok": True, "id": sid}


# --- Projections de lecture (barreau 4) — opérateur plateforme -------------

class RunsInput(BaseModel):
    limit: int = 100

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 100)


class RunInput(BaseModel):
    run_id: str


class DaysInput(BaseModel):
    days: int = 30


class SignalsInput(BaseModel):
    signal: Optional[str] = None
    target: Optional[str] = None
    # open | acknowledged | declined | resolved, ou 'pending' (= à arbitrer :
    # open ∪ acknowledged), ou None (tous).
    status: Optional[str] = None
    limit: int = 200

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 200)


class SetSignalStatusInput(BaseModel):
    signal_id: int
    status: Literal["open", "acknowledged", "declined", "resolved"]
    note: Optional[str] = None     # ce qui a été décidé, et pourquoi


class SignalRow(BaseModel):
    """Un signal tel qu'il est SERVI. Le corps est de la prose libre écrite par un
    agent : il n'a pas de forme, et n'en aura pas."""
    id: int
    created_at: Optional[str] = None
    sub: Optional[str] = None
    email: Optional[str] = None      # rapporteur (LEFT JOIN users) — NULL si compte parti
    name: Optional[str] = None
    org_id: Optional[int] = None
    signal: Optional[str] = None     # tool_feedback | gap
    kind: Optional[str] = None
    target: Optional[str] = None     # feedback : l'outil ; gap : l'intention
    body: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None     # agent | human
    status: Optional[str] = None     # open | acknowledged | declined | resolved
    # Le dernier ARBITRAGE, pas la seule résolution : posé aussi par `acknowledged`
    # et `declined`, effacé par un retour à `open`. Les noms datent des deux états
    # d'origine — c'est `status` qui dit l'état.
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution: Optional[str] = None


class SignalsPage(BaseModel):
    """Une page de signaux + l'état de TOUTE la pile.

    Les deux vont ensemble : une page de 200 lignes ne dit pas si la pile en compte
    203 ou 2 000, et c'est ce chiffre qu'on vient chercher. `counts` porte les quatre
    états — ceux à zéro compris — plus `pending` (open ∪ acknowledged), la seule
    question qu'un opérateur pose vraiment."""
    signals: list[SignalRow]
    counts: dict[str, int]


class SignalArbitrated(BaseModel):
    """Résultat d'un arbitrage. `ok: false` + `error: "not_found"` quand l'id
    n'existe pas — un 404 déguisé en 200, forme historique de cette console."""
    ok: bool
    signal: Optional[SignalRow] = None
    counts: Optional[dict[str, int]] = None
    error: Optional[str] = None
    id: Optional[int] = None       # l'id demandé, quand il est introuvable


def _runs(ctx: ResolvedCtx, inp: RunsInput) -> dict:
    return {"runs": db.list_runs(inp.limit)}


def _run(ctx: ResolvedCtx, inp: RunInput) -> dict:
    return {"run_id": inp.run_id, "calls": db.get_run(inp.run_id)}


def _gaps(ctx: ResolvedCtx, inp: DaysInput) -> dict:
    return {"gaps": db.aggregate_gaps(inp.days)}


def _tool_quality(ctx: ResolvedCtx, inp: DaysInput) -> dict:
    return {"tools": db.aggregate_tool_feedback(inp.days)}


def _signals(ctx: ResolvedCtx, inp: SignalsInput) -> dict:
    _valid = set(db.SIGNAL_STATUSES) | {db.SIGNAL_PENDING}
    if inp.status is not None and inp.status not in _valid:
        raise AuthzDenied(
            400, "unknown_status",
            f"statut inconnu {inp.status!r} — filtre par "
            f"{', '.join(sorted(_valid))}, ou omets-le pour tout voir.")
    # Les COMPTES accompagnent toujours la page : une page de 200 lignes ne dit pas
    # si la pile en compte 203 ou 2 000, et c'est ce chiffre qu'on vient chercher en
    # ouvrant la liste.
    return {"signals": db.list_usage_signals(
                inp.signal, inp.target, inp.limit, status=inp.status),
            "counts": db.count_usage_signals_by_status()}


def _set_signal_status(ctx: ResolvedCtx, inp: SetSignalStatusInput) -> dict:
    # Un refus SANS motif est le défaut qu'on vient fermer sous un autre nom : la
    # pile redeviendrait un endroit où des signaux disparaissent sans qu'on sache
    # pourquoi. `resolved` n'exige rien — le travail livré parle de lui-même, et
    # l'exiger ferait écrire « fait » 200 fois.
    if inp.status == "declined" and not (inp.note or "").strip():
        raise AuthzDenied(
            400, "missing_note",
            "Refuser demande un motif : `note` = pourquoi ce signal ne sera pas "
            "traité. Sans lui, un refus est indistinguable d'un oubli.")
    row = db.set_usage_signal_status(
        inp.signal_id, status=inp.status, by=ctx.sub, note=inp.note)
    if row is None:
        return {"ok": False, "error": "not_found", "id": inp.signal_id}
    return {"ok": True, "signal": row, "counts": db.count_usage_signals_by_status()}


CAPABILITIES += [
    Capability(
        key="usage.feedback", handler=_feedback, Input=FeedbackInput, authz=SUB_ONLY,
        Output=SignalRecorded,
        description="Report a usage signal about oto. signal='tool_feedback' = feedback on a "
                    "tool you just used (target = the tool name ; kind = bug | misleading_doc | "
                    "wrong_result | praise | other). signal='gap' = a use case oto could NOT do, "
                    "call it whenever you wanted to act but no oto capability covered it "
                    "(target = what you were trying to accomplish ; kind = missing_tool | "
                    "missing_doctrine | missing_data | other). text = optional detail.",
        mcp="feedback", rest=RestBinding("POST", "/api/me/usage/feedback"),
    ),
    # --- projections de lecture (opérateur plateforme) ---------------------
    Capability(key="usage.runs", handler=_runs, Input=RunsInput, authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/usage/runs")),
    Capability(key="usage.run", handler=_run, Input=RunInput, authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/usage/runs/{run_id}")),
    Capability(key="usage.gaps", handler=_gaps, Input=DaysInput, authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/usage/gaps")),
    Capability(key="usage.tool_quality", handler=_tool_quality, Input=DaysInput, authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/usage/tool-quality")),
    Capability(key="usage.signals", handler=_signals, Input=SignalsInput, authz=PLATFORM_ADMIN,
               Output=SignalsPage,
               description="List usage signals (feedback/gap) reported about oto, most recent "
                           "first, plus `counts` per status over the WHOLE table. Filters: "
                           "signal ('tool_feedback'|'gap'), target, status "
                           "('open'|'acknowledged'|'declined'|'resolved', or 'pending' = "
                           "everything not yet arbitrated). Platform-admin only.",
               rest=RestBinding("GET", "/api/admin/usage/signals")),
    Capability(key="usage.set_signal_status", handler=_set_signal_status,
               Input=SetSignalStatusInput, authz=PLATFORM_ADMIN,
               Output=SignalArbitrated,
               description="Arbitrate a usage signal. signal_id = the signal's id (from "
                           "usage.signals). status = open (back to the pile, clears the "
                           "arbitration) | acknowledged (read, not decided yet) | declined "
                           "(won't do — `note` REQUIRED, say why) | resolved (done). "
                           "note = what was decided, and why.",
               rest=RestBinding("POST", "/api/admin/usage/signals/{signal_id}/status")),
]
