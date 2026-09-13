"""Ce qu'un run, un agent, un passage ou une organisation a COÛTÉ — `oto_cout`.

Une LECTURE, et rien d'autre : cette capacité ne refuse aucun travail et n'impose
aucune borne. Les plafonds sont un chantier séparé, qui lira cette table sans la
changer. Mesurer d'abord, encadrer ensuite.

**Les quatre `op` sont quatre REGROUPEMENTS du même fait** (une tentative de
travail), pas quatre mesures différentes — cf. `db/runner_job_cost`.

⚠️ **Un total peut être un PLANCHER, et il le dit.** `incomplete` est vrai quand
une tentative du lot n'a pas rendu ses jetons (worker mort avant de conclure) ou
quand un modèle n'est pas tarifé. Un écran qui affiche un montant sans lire ce
drapeau présentera un total amputé comme un total — c'est la faute que ce champ
existe pour empêcher.

⚠️ **Sur la clé d'une org, le montant est une ESTIMATION au tarif public.** Nous
ne voyons ni son tarif négocié ni ses remises. `key_source` porte l'information ;
l'écran doit l'écrire en toutes lettres plutôt que laisser croire à une facture.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .. import db, runner_prix
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class CoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["run", "agent", "fleet", "org"]
    #: `run` — le déroulé à chiffrer (tous ses travaux, `continue` compris).
    run_id: Optional[str] = None
    #: `agent` — le déclencheur.
    trigger_id: Optional[int] = None
    #: `fleet` — le passage.
    fleet_id: Optional[int] = None
    #: Fenêtre en jours pour `agent` et `org`. Sans objet pour `run` et `fleet`,
    #: qui sont des objets FINIS : on les chiffre sur toute leur vie.
    days: int = 30
    #: `org` — ventile le total par `modele`, `source`, `famille` ou `key_source`.
    breakdown: Optional[Literal["modele", "source", "famille", "key_source"]] = None
    #: `run` — rend aussi la ligne de chaque tentative (ce qu'une facture citera).
    detail: bool = False


class Total(BaseModel):
    """Un total et sa qualité. ⚠️ `nano_usd` est un ENTIER de nano-dollars
    (10⁻⁹ USD) : c'est lui qui se somme et se compare. `usd` est rendu pour
    l'affichage seul."""
    attempts: Optional[int] = None
    runs: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    #: Le total est un PLANCHER : une tentative au moins n'a pas rendu ses jetons,
    #: ou porte un modèle que le barème ne tarife pas.
    incomplete: Optional[bool] = None
    #: Les deux causes, séparées — elles ne se réparent pas pareil.
    missing_tokens: Optional[bool] = None
    unpriced: Optional[bool] = None


class Ligne(BaseModel):
    """Une tentative. Le grain que citera une facture."""
    job_id: Optional[int] = None
    attempt: Optional[int] = None
    run_id: Optional[str] = None
    source: Optional[str] = None
    modele: Optional[str] = None
    key_source: Optional[str] = None
    outcome: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    nano_usd: Optional[int] = None
    #: Le barème qui a produit le montant — c'est lui qui rend un total
    #: EXPLICABLE six mois plus tard, quand les prix auront changé.
    bareme: Optional[str] = None
    steps: Optional[int] = None
    finished_at: Optional[str] = None


class Part(BaseModel):
    """Une part de la ventilation."""
    cle: Optional[str] = None
    attempts: Optional[int] = None
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    missing_tokens: Optional[bool] = None


class CoutOut(BaseModel):
    total: Optional[Total] = None
    lines: Optional[list[Ligne]] = None
    breakdown: Optional[list[Part]] = None
    #: Le barème courant, pour qu'un écran puisse dire à quel tarif il chiffre.
    bareme: Optional[str] = None


def _total(d: dict) -> dict:
    """La forme SERVIE d'une somme : les noms du modèle de sortie, plus les
    dollars pour l'affichage."""
    nano = d.get("nano_usd")
    return {
        "attempts": d.get("tentatives"),
        "runs": d.get("runs"),
        "input_tokens": d.get("input_tokens"),
        "output_tokens": d.get("output_tokens"),
        "cache_write_tokens": d.get("cache_write_tokens"),
        "cache_read_tokens": d.get("cache_read_tokens"),
        "nano_usd": nano,
        "usd": runner_prix.en_dollars(nano),
        "incomplete": d.get("incomplet"),
        "missing_tokens": d.get("jetons_manquants"),
        "unpriced": d.get("non_tarifes"),
    }


def _cout(ctx: ResolvedCtx, inp: CoutInput) -> dict:
    if ctx.org_id is None:
        raise AuthzDenied(400, "no_active_org",
                          "le coût se lit dans une organisation : aucune n'est active")
    rendu: dict = {"bareme": runner_prix.BAREME_COURANT}

    if inp.op == "run":
        if not inp.run_id:
            raise AuthzDenied(400, "missing_fields", "`run` exige `run_id`")
        # Org-scopé par la requête : le run d'une autre org rend un total VIDE,
        # jamais le sien — même règle que partout ailleurs, et pas d'oracle
        # d'existence.
        rendu["total"] = _total(db.cout_du_run(inp.run_id, ctx.org_id))
        if inp.detail:
            rendu["lines"] = db.lignes_du_run(inp.run_id, ctx.org_id)
        return rendu

    if inp.op == "agent":
        if inp.trigger_id is None:
            raise AuthzDenied(400, "missing_fields", "`agent` exige `trigger_id`")
        rendu["total"] = _total(
            db.cout_de_l_agent(inp.trigger_id, ctx.org_id, jours=inp.days))
        return rendu

    if inp.op == "fleet":
        if inp.fleet_id is None:
            raise AuthzDenied(400, "missing_fields", "`fleet` exige `fleet_id`")
        rendu["total"] = _total(db.cout_du_passage(inp.fleet_id, ctx.org_id))
        return rendu

    rendu["total"] = _total(db.cout_de_l_org(ctx.org_id, jours=inp.days))
    if inp.breakdown:
        rendu["breakdown"] = [
            {"cle": p.get("cle"), "attempts": p.get("tentatives"),
             "nano_usd": p.get("nano_usd"),
             "usd": runner_prix.en_dollars(p.get("nano_usd")),
             "input_tokens": p.get("input_tokens"),
             "output_tokens": p.get("output_tokens"),
             "missing_tokens": p.get("jetons_manquants")}
            for p in db.ventilation(ctx.org_id, jours=inp.days, par=inp.breakdown)]
    return rendu


CAPABILITIES += [
    Capability(
        key="runner.cost",
        handler=_cout,
        Input=CoutInput,
        Output=CoutOut,
        authz=ORG_MEMBER,
        mcp="oto_cout",
        description=(
            "What a hosted agent's work COST, in tokens and US dollars. Four "
            "groupings of one fact — a job attempt: op=run (a whole run, including "
            "the jobs a `continue` added), op=agent (a trigger, over `days`), "
            "op=fleet (a whole pass), op=org (this workspace, over `days`, with an "
            "optional `breakdown` by model, source, family or key_source). "
            "⚠️ Read `incomplete` before showing any amount: a job that died "
            "without reporting, or a model with no published price, makes the "
            "total a FLOOR rather than a total. ⚠️ On an organisation's own model "
            "key the amount is an ESTIMATE at public list price — we cannot see "
            "its negotiated rate. Reading only: this imposes no limit and refuses "
            "no work."),
        rest=RestBinding("POST", "/api/me/runner/cost"),
        errors=(
            DeclaredError(400, "missing_fields",
                          "`run`/`agent`/`fleet` sans l'identifiant de leur objet"),
            DeclaredError(400, "no_active_org",
                          "aucune organisation active — le coût n'a pas de périmètre"),
        ),
    ),
]
