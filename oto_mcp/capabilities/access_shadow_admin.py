"""Capacité admin : lire la fenêtre de double lecture L7 (blueprint ADR 0053).

La fenêtre décide d'un lot IRRÉVERSIBLE — retourner l'autorité vers la chaîne de
grants, puis retirer `walk_cascade` et `connector_acl`. Sa porte est une phrase
courte : *zéro divergence de classe « inconnu » pendant N jours, avec un
dénominateur non nul*. Cette capacité rend cette phrase lisible **sans `psql` sur
la base partagée** — c'est sa seule raison d'être.

Lecture seule, `PLATFORM_ADMIN`, comme les autres lentilles de supervision. Rien
n'est écrit ici : le compteur est alimenté par le chemin de résolution
(`access/chain_shadow.py`), et l'éteindre est un cran d'environnement
(`OTO_L7_SHADOW=0`), pas un geste d'API — sinon on pourrait couper la mesure depuis
la surface même qui sert à la lire.

⚠️ **Par-process, comme le registre des tenants** : prod et préprod partagent la
base mais pas leur trafic. Une fenêtre lue ici agrège les deux ; le verdict se
prononce sur la prod, qui est la seule à porter du trafic réel.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from .. import config
from ..access import chain_shadow
from ..db import access_shadow as db_shadow
from ._authz import PLATFORM_ADMIN
from ._types import Capability, ResolvedCtx, RestBinding, cap_limit

from .registry import CAPABILITIES

_DEFAULT_DAYS = 7
_MAX_DAYS = 365


class AccessShadowInput(BaseModel):
    # UN seul op déclaré, et c'est celui qui existe : une description qui promet un
    # verbe absent est un piège vécu sur les autres consoles.
    op: Literal["read"] = "read"
    days: int = _DEFAULT_DAYS
    connector: Optional[str] = None
    classe: Optional[str] = None
    # **`prod` par défaut, et c'est le fond du réglage** : prod et preprod partagent
    # la base, donc « la fenêtre est concluante » ne veut rien dire tant qu'on n'a pas
    # dit QUI a écrit. Une fenêtre de prod ne compte que ce que la prod a écrit.
    # `toutes` rend aussi la preprod ET les lignes d'origine inconnue (celles d'avant
    # la colonne) — utile pour lire l'historique, jamais pour prononcer un verdict.
    origine: Literal["prod", "preprod", "toutes"] = "prod"

    @field_validator("days")
    @classmethod
    def _cap_days(cls, v):
        return cap_limit(v, _MAX_DAYS, default=_DEFAULT_DAYS)


# ── forme SERVIE (ADR 0059 : ce qui n'est pas déclaré n'est pas opposable) ────

class ShadowLigne(BaseModel):
    # `None` = ligne écrite avant que l'origine ne soit notée : ambiguë, et elle le dit.
    origine: Optional[str] = None
    day: str
    connector: str
    org_id: int
    classe: str
    n: int
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    sample: dict = {}


class ShadowVerdict(BaseModel):
    # L'origine que ce verdict COUVRE — dit dans la réponse, jamais sous-entendu.
    origine: str
    # Observations écartées faute d'origine connue (lignes d'avant la colonne). Une
    # fenêtre qui paraît vide alors que 134 observations dorment en « inconnu » doit
    # pouvoir se lire comme telle.
    origine_inconnue: int
    observations: int
    par_classe: dict
    inconnus: int
    # La porte vers la PR 2, calculée : pas de divergence inexpliquée ET un
    # dénominateur non nul. Les deux moitiés, jamais une seule.
    porte_ouverte: bool
    shadow_actif: bool


class ShadowOut(BaseModel):
    days: int
    origine: str
    classes: list[str]
    verdict: ShadowVerdict
    lignes: list[ShadowLigne]


def _verdict(lignes: list[dict], origine: str = config.PROD,
             origine_inconnue: int = 0) -> dict:
    """La phrase que la fenêtre doit rendre, calculée et non racontée.

    `porte_ouverte` n'est vrai que si les DEUX moitiés le sont : aucune divergence
    inexpliquée, et un dénominateur non nul. Une fenêtre muette (le shadow éteint,
    un connecteur jamais résolu) rendrait « zéro inconnu » sans rien prouver — c'est
    précisément le faux vert qu'on veut rendre impossible à lire."""
    par_classe: dict = {}
    for r in lignes:
        par_classe[r["classe"]] = par_classe.get(r["classe"], 0) + int(r["n"])
    observations = sum(par_classe.values())
    inconnus = par_classe.get(chain_shadow.INCONNU, 0)
    return {
        "origine": origine,
        "origine_inconnue": origine_inconnue,
        "observations": observations,
        "par_classe": {c: par_classe.get(c, 0) for c in chain_shadow.CLASSES},
        "inconnus": inconnus,
        "porte_ouverte": bool(observations) and inconnus == 0,
        "shadow_actif": chain_shadow._enabled(),
    }


def _read(ctx: ResolvedCtx, inp: AccessShadowInput) -> dict:
    filtre = None if inp.origine == "toutes" else inp.origine
    lignes = db_shadow.read_shadow(days=inp.days, connector=inp.connector,
                                   classe=inp.classe, origine=filtre)
    # Compté sur la MÊME fenêtre, sans le filtre d'origine : c'est ce qui permet de
    # distinguer « rien ne s'est passé » de « tout est tombé dans l'inconnu ».
    inconnues = sum(int(r["n"]) for r in db_shadow.read_shadow(
        days=inp.days, connector=inp.connector, classe=inp.classe)
        if not r.get("origine"))
    return {
        "days": inp.days,
        "origine": inp.origine,
        "classes": list(chain_shadow.CLASSES),
        "verdict": _verdict(lignes, origine=inp.origine,
                            origine_inconnue=inconnues),
        # ⚠️ **Les dates arrivent DÉJÀ en chaînes ISO** : le row factory du pool
        # (`db._conn._str_dict_row`) normalise tout `datetime`/`date` avant qu'une
        # ligne n'atteigne un appelant — c'est l'invariant de tout le package `db`,
        # et c'est pourquoi aucun autre consommateur ne convertit. Rappeler
        # `.isoformat()` par-dessus ne peut donner qu'`AttributeError: 'str' object
        # has no attribute 'isoformat'`, soit « Erreur interne du serveur » pour
        # l'admin qui lit sa fenêtre : vécu en prod le 2026-08-29 (v1.161.0), avec
        # une table présente et un compteur qui écrivait — seule la LECTURE cassait.
        # Rejeu contre un vrai PostgreSQL : `tests/test_l7_shadow_lens_live.py`.
        "lignes": [
            {"day": r["day"], "connector": r["connector"],
             "org_id": int(r["org_id"]), "classe": r["classe"], "n": int(r["n"]),
             "first_at": r.get("first_at"), "last_at": r.get("last_at"),
             "origine": r.get("origine"), "sample": r.get("sample") or {}}
            for r in lignes
        ],
    }


CAPABILITIES += [
    Capability(
        key="admin.access_shadow", handler=_read, Input=AccessShadowInput,
        Output=ShadowOut, authz=PLATFORM_ADMIN,
        description=(
            "[platform admin] L7 shadow window (ADR 0053): during the double-read "
            "window the grant CHAIN computes alongside while the legacy cascade "
            "decides; every pair of verdicts is compared and filed under a closed set "
            "of classes. op=read (`days`, default 7; optional `connector`, `classe`) → "
            "one row per day x connector x org x class, with the first sample of the "
            "day (sub is hashed, never in clear), plus a `verdict` block. Four "
            "divergence classes are EXPECTED and named by the ADR — "
            "`elargissement_equipe` (the chain reads every team of the caller, the "
            "cascade only the ACTIVE one; counted per org because it changes served "
            "behaviour), `restriction_acl` (D1 dissolves connector_acl), "
            "`free_tier_hors_modele` (an open platform key has no grantee in 0053 — "
            "an explicit everyone-edge lands next, so this class must reach zero "
            "before the removal), "
            "`partage_hors_modele` (its sibling: a platform key CLOSED on an allowlist "
            "that no edge accounts for — the NOMINAL edges are missing, L5 only seeded "
            "the switched connectors), "
            "`perso_cross_org` (the cascade follows a personal key across orgs). Only "
            "`inconnu` must stay at zero: `verdict.porte_ouverte` is true when there "
            "is no unknown divergence AND the denominator is non-zero, for the origin "
            "it names; `verdict.origine_inconnue` counts observations left out because "
            "they predate the origin column. Read-only — "
            "the counter is fed by the resolution path and switched off by the "
            "OTO_L7_SHADOW env, never from here."),
        mcp="oto_admin_access_shadow",
        rest=RestBinding("GET", "/api/admin/access-shadow"),
    ),
]
