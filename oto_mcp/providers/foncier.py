"""Déclaration de registre du connecteur `foncier`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# foncier / sante : connecteurs open-data déclarés (ADR 0010). Inertes tant
# que non activés en DB (connector_activation) — register_all gate dessus,
# donc absents du seed initial → OFF par défaut (deny-by-default).
CONNECTOR = _c(
    "foncier", ["foncier"], secret_kind="none",
    label="Foncier", help="géocodage, cadastre, bâti, risques/ICPE, solaire, immobilier (open data)",
)

CATEGORY = "Data FR"
PUBLISHER = "État (open data)"
DESCRIPTION = (
    "Le foncier et l'immobilier français en open data : géocodage "
    "BAN, parcelles cadastrales, bâti, transactions DVF (prix au m², "
    "comparables par adresse), risques et ICPE, DPE, consommation "
    "électrique et productible solaire."
)
LOGO_DOMAIN = "data.gouv.fr"
