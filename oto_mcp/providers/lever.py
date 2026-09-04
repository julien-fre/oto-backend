"""Déclaration de registre du connecteur `lever`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "lever", ["lever"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Lever",
    help="ATS — opportunities (candidats), postings, stages, notes",
    href="https://www.lever.co",
)

CATEGORY = "Recrutement"
PUBLISHER = "Lever"
LOGO_DOMAIN = "lever.co"

DESCRIPTION = (
    "Le recrutement suivi dans Lever (ATS) : opportunities (candidats), "
    "postings (offres), étapes du pipeline (stages) et notes."
)
