"""Capacités « le run » — son fil (append/read, op-aware, chantier runner R1) et son
CYCLE en REST (ouvrir, clore — oto#227).

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

**Le cycle en REST (oto#227).** Un consommateur REST qui réservait une ligne ne pouvait
pas y écrire : la garde du bail ne reconnaît le titulaire QUE par son run, et ouvrir ou
clore un run n'existait qu'en MCP (`run_start`/`run_finish`). Les deux routes ci-dessous
écrivent les MÊMES faits au journal que le MCP — un run se reconstruit de ses faits,
jamais de `runs` (`db.usage`) — et l'en-tête `X-Oto-Run` (`run_de_l_en_tete`, posé par
`_rest_adapter`) porte ensuite le run sur réservation, écriture et libération, qui ne
changent pas. ⚠️ Un run n'est ni une identité ni un droit : il ne distingue pas deux
sessions d'un même jeton. C'est au consommateur de lier SON run à SA session, et de ne
jamais reprendre un `_claimed_run` lu sur une ligne.
"""
from __future__ import annotations

import json
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import access, db, ownership, roles, run_status, session_org
from ._authz import ORG_MEMBER, SUB_ONLY
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
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
    # Vue bornée (oto#270) : un run d'une autre org n'existe pas ici — même 404 que
    # « inconnu », pour ne pas dire qu'il existe ailleurs.
    borne = ownership.vue_bornee()
    if borne is not None and head.get("org_id") != borne:
        raise AuthzDenied(404, "run_not_found", "run inconnu")
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


# ── Le cycle du run en REST (oto#227) ─────────────────────────────────────────

#: Les refus que l'en-tête peut rendre, DÉCLARÉS ici, à côté du contrôle qui les lève, et
#: relus par le générateur OpenAPI sur toute opération de capacité (#217 : un refus se
#: déclare là où il peut survenir). Le porteur non membre de l'org du run n'y figure pas :
#: il reçoit la 403 `forbidden` générique, déjà déclarée sur chaque opération.
_RUN_INCONNU = DeclaredError(
    404, "run_not_found", "`X-Oto-Run` désigne un run inconnu, ou le run d'un autre compte")
_RUN_ORG_CONTREDITE = DeclaredError(
    400, "run_org_mismatch", "`X-Oto-Org` désigne une autre org que celle du run de `X-Oto-Run`")
_RUN_CLOS = DeclaredError(409, "run_closed", "le run de `X-Oto-Run` est clos")
REFUS_DECLARES_DE_L_EN_TETE = (_RUN_ORG_CONTREDITE, _RUN_INCONNU, _RUN_CLOS)


def run_de_l_en_tete(sub: Optional[str], run_id: str) -> Optional[int]:
    """Juge l'en-tête `X-Oto-Run` d'une requête REST — sync (threadpool), UNE requête SQL
    (`db.usage.run_pour_en_tete`). Rend l'org du run à poser (None : run hors org), ou
    lève un refus NOMMÉ, avant que la capacité ne touche quoi que ce soit :

    - run inconnu, ou run d'un AUTRE compte → 404 `run_not_found`, sans oracle (même
      refus que le fil). ⚠️ Plus strict que l'axe MCP, qui ne garde que l'appartenance :
      en REST il n'y a pas de pile de session, le run est tout ce qui désigne le titulaire ;
    - porteur non membre de l'org du run → 403 `forbidden`, jamais un repli sur son org ;
    - `X-Oto-Org` qui désigne une autre org → 400 `run_org_mismatch` : l'org du run prime
      sur la consultation (`access.current_org`), et l'ignorer en silence ferait écrire
      ailleurs que là où l'appelant croit écrire ;
    - run CLOS (fait `run_finish`) → 409 `run_closed`.

    L'appartenance est celle de `roles.effective_org_role` — rôle dans l'org, ou escalade
    super_admin (de base ou d'amorçage) — relue des colonnes que rend la requête unique."""
    from ..db import usage as db_usage
    run = db_usage.run_pour_en_tete(run_id, sub or "")
    if not run or not sub or run["sub"] != sub:
        raise AuthzDenied(_RUN_INCONNU.status, _RUN_INCONNU.code,
                          "run inconnu — ouvre le tien avec `POST /api/me/runs`.")
    org = run["org_id"]
    if org is not None:
        org = int(org)
        super_admin = (run["role"] == access.SUPER_ADMIN
                       or sub == os.environ.get("OTO_MCP_ADMIN_SUB"))
        if run["org_role"] is None and not super_admin:
            raise AuthzDenied(403, "forbidden",
                              f"Le run se déroule dans l'org #{org}, dont tu n'es pas membre.")
        vue = session_org.current_view_org()
        if vue is not None and vue != org:
            raise AuthzDenied(
                _RUN_ORG_CONTREDITE.status, _RUN_ORG_CONTREDITE.code,
                f"`X-Oto-Org` désigne l'org #{vue}, et le run se déroule dans l'org #{org} "
                "— aligne les deux en-têtes, ou retire `X-Oto-Org`.")
    if run["clos"]:
        raise AuthzDenied(_RUN_CLOS.status, _RUN_CLOS.code,
                          "run clos — ouvre-en un nouveau avec `POST /api/me/runs`.")
    return org


class RunOuvertureInput(BaseModel):
    label: str
    guide: Optional[str] = None


class RunClotureInput(BaseModel):
    run_id: str
    outcome: str
    note: Optional[str] = None


class RunCycleOut(BaseModel):
    run_id: str
    org_id: Optional[int] = None
    label: Optional[str] = None
    guide: Optional[str] = None
    guide_version: Optional[int] = None
    outcome: Optional[str] = None
    rows_released: Optional[int] = None


def _fait(sub: str, tool: str, run_id: str, org_id: Optional[int], args: dict) -> None:
    """Le FAIT de journal, écrit comme le MCP l'écrit. ⚠️ Sans lui le run n'existe pas :
    le suivi et la clôture se lisent des faits `run_start`/`run_finish`, et une requête
    REST n'est journalisée que sous sa route.

    ⚠️ Les arguments passent par `calllog.truncated_args`, comme ceux du calllog MCP :
    c'est elle qui tronque et masque, et une écriture directe ferait mentir les deux
    surfaces qui lisent ces arguments (`tests/test_timeline_args_declare.py`)."""
    from .. import calllog
    db.insert_tool_call({"kind": "rest", "sub": sub, "tool": tool, "run_id": run_id,
                         "org_id": org_id, "ok": True,
                         "args": calllog.truncated_args(args, tool=tool)})


def _ouvrir(ctx: ResolvedCtx, inp: RunOuvertureInput) -> dict:
    from .. import guide_run as pile
    from ..db import usage as db_usage
    from ..tools import guide_run as outils_run
    label = inp.label.strip()
    if not label:
        raise AuthzDenied(400, "missing_fields", "`label` dit ce que fait le run")
    run_id = pile.new_run_id()
    version = outils_run.version_de_procedure(ctx.sub, inp.guide) if inp.guide else None
    # Les clés sont celles que lit la reconstruction du run (`db.usage`), prises à sa
    # constante plutôt que réécrites : elles voyagent dans le journal.
    _fait(ctx.sub, "run_start", run_id, ctx.org_id,
          {"label": label, db_usage._ARG_PROCEDURE: inp.guide,
           db_usage._ARG_PROCEDURE + "_version": version})
    # L'index APRÈS le fait : c'est lui que lit l'en-tête `X-Oto-Run`, et un run sans fait
    # serait un titulaire que le suivi ne montrerait jamais.
    db.insert_run(run_id, sub=ctx.sub, org_id=ctx.org_id, label=label, guide=inp.guide)
    return {"run_id": run_id, "org_id": ctx.org_id, "label": label, "guide": inp.guide,
            "guide_version": version}


def _clore(ctx: ResolvedCtx, inp: RunClotureInput) -> dict:
    from ..tools import guide_run as outils_run
    head = db.get_run_head(inp.run_id)
    if not head or head.get("sub") != ctx.sub:
        raise AuthzDenied(404, "run_not_found", "run inconnu")
    refus = run_status.refus_de_cloture(inp.outcome, inp.note)
    if refus:
        raise AuthzDenied(400, *refus)
    _fait(ctx.sub, "run_finish", inp.run_id, head.get("org_id"),
          {"run_id": inp.run_id, "outcome": inp.outcome, "note": inp.note})
    db.finish_run(inp.run_id, inp.outcome, inp.note, sub=ctx.sub)
    # Un run clos ne tient plus rien : ses lignes reviennent à la file, comme à `run_finish`.
    return {"run_id": inp.run_id, "org_id": head.get("org_id"), "outcome": inp.outcome,
            "rows_released": outils_run.liberer_les_lignes_du_run(inp.run_id)}


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
    Capability(
        key="runs.open",
        handler=_ouvrir,
        Input=RunOuvertureInput,
        Output=RunCycleOut,
        authz=ORG_MEMBER,
        mcp=None,   # le MCP a `run_start`
        rest=RestBinding(verb="POST", path="/api/me/runs", status=201),
        description=(
            "Open a RUN over REST, in the active org (or `X-Oto-Org`). The run is the "
            "only holder a row lease recognises: send its id as `X-Oto-Run` on "
            "claim_next / claim / PATCH row / release so the lease and the write are "
            "yours. A run is not an identity: bind it to YOUR session, never reuse a "
            "`_claimed_run` read on a row."
        ),
    ),
    Capability(
        key="runs.close",
        handler=_clore,
        Input=RunClotureInput,
        Output=RunCycleOut,
        authz=SUB_ONLY,
        mcp=None,   # le MCP a `run_finish`
        rest=RestBinding(verb="PATCH", path="/api/me/runs/{run_id}"),
        description=(
            "Close YOUR run: `outcome` done | partial | failed | blocked, and a `note`. "
            "`partial` = the run ended cleanly but did only part of the work: `note` is "
            "then required and says what is done and what remains (400 note_required). "
            "The rows it still held go back to the queue (`rows_released`, 0 written). "
            "A closed run is refused as `X-Oto-Run` (409 run_closed)."
        ),
    ),
]
