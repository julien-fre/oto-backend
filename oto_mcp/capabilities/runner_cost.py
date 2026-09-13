"""Consommation et Coûts des agents hébergés — `oto_cost` (capacité `runner.cost`).

Deux mesures, jamais confondues : la **Consommation** (les jetons, poste par poste) et
les **Coûts** (un montant, au tarif public). Toutes deux se lisent sur les TENTATIVES
(`runner_job_attempts`, source unique partagée avec oto#196 et oto#197) ; « run »,
« agent », « passage » et « organisation » n'en sont que des regroupements.

**Réservée aux ADMINISTRATEURS de l'org** (`ORG_ADMIN`, super_admin par escalade) :
les Coûts d'une organisation sont une information de gouvernance, pas de travail. Un
membre reçoit le refus nommé de la garde, qui dit qui peut lire.

Une LECTURE, et rien d'autre : cette capacité ne refuse aucun travail et n'impose
aucune borne. Les plafonds sont un autre chantier.

⚠️ **Un total peut être un PLANCHER, et il le dit** : `incomplete` et
`incomplete_reasons`, au total comme à chaque part. ⚠️ **Un montant peut reposer sur
un prix PROVISOIRE, et il le dit aussi** : `price_unverified`, à la ligne, à la part et
au total — un montant provisoire existe, un montant absent est `null` avec sa raison.

⚠️ `Output` DÉCRIT, il ne valide pas : chaque forme servie ci-dessous est construite
champ par champ, et un banc confronte les clés produites aux modèles dans les deux
sens.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .. import db, runner_prix
from ._authz import ORG_ADMIN
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding, cap_limit
from .registry import CAPABILITIES

JOURS_DEFAUT = 30
JOURS_MAX = 366
_POSTES = ("usage_input", "usage_output", "usage_cache_read", "usage_cache_write",
           "usage_input_total")
_BESOIN = {"run": "run_id", "agent": "trigger_id", "fleet": "fleet_id"}


class CostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["run", "agent", "fleet", "org"] = Field(description=(
        "Le regroupement : `run` (un déroulé, tous ses travaux, `continue` compris), "
        "`agent` (un déclencheur, sur `days`), `fleet` (un passage entier), `org` "
        "(l'organisation active, sur `days`)."))
    run_id: Optional[str] = Field(None, description="`op=run` : le déroulé.")
    trigger_id: Optional[int] = Field(None, description="`op=agent` : le déclencheur.")
    fleet_id: Optional[int] = Field(None, description="`op=fleet` : le passage.")
    days: Optional[int] = Field(None, description=(
        "`agent` et `org` : la fenêtre en jours (30 par défaut, écrêtée entre 1 et 366). "
        "La fenêtre appliquée est rendue dans `window_days`."))
    breakdown: Optional[Literal["model", "source", "provider_family", "key_source"]] = Field(
        None, description="Ventile le regroupement par cette clé ; chaque part dit si elle est incomplète.")
    detail: bool = Field(False, description=(
        "`op=run` seulement : rend aussi une ligne par tentative (100 au plus ; "
        "`lines_truncated` dit s'il en reste)."))


class CostTotal(BaseModel):
    """Consommation et Coûts d'un regroupement. ⚠️ `nano_usd` est un ENTIER de
    nano-dollars (10⁻⁹ USD) : c'est lui qui se somme ; `usd` n'est que l'affichage.
    Les postes et le montant ne comptent que les tentatives ATTESTÉES."""
    attempts: int
    runs: int
    open: int
    lost: int
    attested: int
    not_attested: int
    unmeasured: int
    usage_input: Optional[int] = None
    usage_output: Optional[int] = None
    usage_cache_read: Optional[int] = None
    usage_cache_write: Optional[int] = None
    #: Entrée TOTALE déclarée, cache compris : un majorant, jamais un montant.
    usage_input_total: Optional[int] = None
    budget_units: Optional[int] = None
    budget_units_unknown: int
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    unpriced: int
    unpriced_reasons: dict[str, int]
    #: Le montant contient des Coûts calculés au prix PROVISOIRE d'un barème non vérifié.
    price_unverified: bool
    price_unverified_attempts: int
    incomplete: bool
    incomplete_reasons: list[str]


class CostNotAttested(BaseModel):
    """Les tentatives dont les nombres ne sont PAS ATTESTÉS (résultat sans
    `usage_couverture`) : gardés, mais jamais additionnés au total."""
    attempts: int
    usage_input: Optional[int] = None
    usage_output: Optional[int] = None
    usage_cache_read: Optional[int] = None
    usage_cache_write: Optional[int] = None
    usage_input_total: Optional[int] = None
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    price_unverified: bool


class CostPart(BaseModel):
    """Une part de la ventilation, avec son propre aveu d'incomplétude."""
    key: Optional[str] = None
    attempts: int
    open: int
    lost: int
    not_attested: int
    unmeasured: int
    usage_input: Optional[int] = None
    usage_output: Optional[int] = None
    usage_cache_read: Optional[int] = None
    usage_cache_write: Optional[int] = None
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    unpriced: int
    price_unverified: bool
    price_unverified_attempts: int
    incomplete: bool


class CostLine(BaseModel):
    """Une tentative — le grain qu'une facture citera."""
    attempt_id: str
    job_id: int
    attempt_no: int
    run_id: Optional[str] = None
    trigger_id: Optional[int] = None
    fleet_id: Optional[int] = None
    source: str
    provider_family: Optional[str] = None
    model: Optional[str] = None
    key_source: Optional[str] = None
    outcome: str
    stopped: Optional[str] = None
    steps: Optional[int] = None
    claimed_at: str
    ended_at: Optional[str] = None
    usage_input: Optional[int] = None
    usage_output: Optional[int] = None
    usage_cache_read: Optional[int] = None
    usage_cache_write: Optional[int] = None
    usage_input_total: Optional[int] = None
    attested: bool
    budget_units: Optional[int] = None
    nano_usd: Optional[int] = None
    usd: Optional[float] = None
    #: Le barème qui a produit le montant — ce qui le rend explicable plus tard.
    bareme: Optional[str] = None
    unpriced_reason: Optional[str] = None
    #: Montant calculé au prix PROVISOIRE (motif `usage_price_unverified`).
    price_unverified: bool


class CostOut(BaseModel):
    bareme: str
    #: Le premier fait enregistré. Un regroupement ancré avant lui est incomplet.
    measured_since: Optional[str] = None
    window_days: Optional[int] = None
    total: CostTotal
    not_attested: CostNotAttested
    breakdown: Optional[list[CostPart]] = None
    lines: Optional[list[CostLine]] = None
    lines_truncated: Optional[bool] = None


def _raisons(a: dict, avant_mesure: bool) -> list[str]:
    raisons = []
    if avant_mesure:
        raisons.append("before_measurement")
    for nom, compte in (("open_attempts", "open"), ("unmeasured_attempts", "unmeasured"),
                        ("not_attested", "not_attested"), ("unpriced", "unpriced")):
        if a[compte]:
            raisons.append(nom)
    return raisons


def _total(a: dict, avant_mesure: bool) -> dict:
    raisons = _raisons(a, avant_mesure)
    return {
        **{k: a[k] for k in ("attempts", "runs", "open", "lost", "attested",
                             "not_attested", "unmeasured", "budget_units",
                             "budget_units_unknown", "nano_usd", "unpriced",
                             "price_unverified_attempts")},
        **{p: a[p] for p in _POSTES},
        "usd": runner_prix.en_dollars(a["nano_usd"]),
        "unpriced_reasons": {r: a[f"raison_{r}"] for r in runner_prix.RAISONS
                             if a[f"raison_{r}"]},
        "price_unverified": bool(a["price_unverified_attempts"]),
        "incomplete": bool(raisons),
        "incomplete_reasons": raisons,
    }


def _non_attestees(a: dict) -> dict:
    return {"attempts": a["not_attested"], **{p: a[f"na_{p}"] for p in _POSTES},
            "nano_usd": a["na_nano_usd"], "usd": runner_prix.en_dollars(a["na_nano_usd"]),
            "price_unverified": bool(a["na_price_unverified_attempts"])}


def _part(a: dict, avant_mesure: bool) -> dict:
    return {"key": a["key"],
            **{k: a[k] for k in ("attempts", "open", "lost", "not_attested", "unmeasured",
                                 "nano_usd", "unpriced", "price_unverified_attempts")},
            **{p: a[p] for p in _POSTES if p != "usage_input_total"},
            "usd": runner_prix.en_dollars(a["nano_usd"]),
            "price_unverified": bool(a["price_unverified_attempts"]),
            "incomplete": bool(_raisons(a, avant_mesure))}


def _ligne(r: dict) -> dict:
    return {**{k: r[k] for k in CostLine.model_fields if k != "usd"},
            "usd": runner_prix.en_dollars(r["nano_usd"])}


def _cost(ctx: ResolvedCtx, inp: CostInput) -> dict:
    besoin = _BESOIN.get(inp.op)
    if besoin and getattr(inp, besoin) in (None, ""):
        raise AuthzDenied(400, "missing_fields", f"op={inp.op} requires `{besoin}`")
    if inp.detail and inp.op != "run":
        raise AuthzDenied(400, "detail_requires_run",
                          "`detail` lists the attempts of ONE run: use op=run")
    fenetree = inp.op in ("agent", "org")
    jours = cap_limit(inp.days, JOURS_MAX, default=JOURS_DEFAUT)
    portee = {"run_id": inp.run_id, "trigger_id": inp.trigger_id,
              "fleet_id": inp.fleet_id, "jours": jours}
    mesure = db.cout_des_tentatives(ctx.org_id, inp.op, **portee)
    avant = bool(mesure["before_measurement"])
    rendu = {
        "bareme": runner_prix.BAREME_COURANT,
        "measured_since": mesure["measured_since"],
        "window_days": jours if fenetree else None,
        "total": _total(mesure["agregat"], avant),
        "not_attested": _non_attestees(mesure["agregat"]),
    }
    if inp.breakdown:
        rendu["breakdown"] = [
            _part(p, avant) for p in db.ventilation_des_tentatives(
                ctx.org_id, inp.op, inp.breakdown, **portee)]
    if inp.detail:
        lignes, tronquee = db.lignes_des_tentatives(ctx.org_id, inp.run_id)
        rendu["lines"] = [_ligne(r) for r in lignes]
        rendu["lines_truncated"] = tronquee
    return rendu


CAPABILITIES += [
    Capability(
        key="runner.cost",
        handler=_cost,
        Input=CostInput,
        Output=CostOut,
        authz=ORG_ADMIN,
        mcp="oto_cost",
        rest=RestBinding("POST", "/api/me/runner/cost"),
        description=(
            "Consommation (les jetons, poste par poste) et Coûts (un montant en dollars, "
            "au tarif public) des agents hébergés, lus sur chaque tentative de travail. "
            "Réservé aux administrateurs de l'organisation active. "
            "op=run (un déroulé, `continue` compris), op=agent (un déclencheur, sur "
            "`days`), op=fleet (un passage entier), op=org (l'organisation, sur `days`) ; "
            "`breakdown` ventile par modèle, source, famille de fournisseur ou payeur. "
            "⚠️ Lire `total.incomplete` et `incomplete_reasons` AVANT d'afficher un "
            "montant : une tentative ouverte, perdue sans mesure, non attestée ou non "
            "tarifée fait du total un PLANCHER. ⚠️ `price_unverified` : une part du "
            "montant repose sur un prix PROVISOIRE, à ne pas présenter comme vérifié. "
            "⚠️ Sur la clé d'une organisation (`key_source=org`), le montant est une "
            "ESTIMATION au tarif public. Lecture seule : ne refuse aucun travail et "
            "n'impose aucune borne."),
        errors=(
            DeclaredError(400, "missing_fields",
                          "`run`, `agent` ou `fleet` sans l'identifiant de leur objet"),
            DeclaredError(400, "detail_requires_run",
                          "`detail` demandé sur un autre regroupement que `run`"),
        ),
    ),
]
