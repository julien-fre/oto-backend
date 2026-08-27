"""Déclaration de registre du connecteur `culture`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "culture", ["culture"], secret_kind="none",
    label="Culture (open data)",
    help="entreprises du spectacle vivant — open data Ministère de la Culture",
)

CATEGORY = "Data FR"
PUBLISHER = "Ministère de la Culture"
DESCRIPTION = (
    "Les entreprises du spectacle vivant, en open data du Ministère "
    "de la Culture : recherche multicritère, fiches détaillées, "
    "statistiques sectorielles et export."
)
LOGO_DOMAIN = "culture.gouv.fr"
