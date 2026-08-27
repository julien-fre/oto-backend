"""Déclaration de registre du connecteur `urba`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "urba", ["urba"], secret_kind="none",
    label="Urbanisme", help="zonage PLU/GPU, risques, QPV, EPFIF, socio-démo commune (open data)",
)

DESCRIPTION = (
    "L'urbanisme réglementaire en open data : zonage PLU/GPU et "
    "règlements, risques naturels, argiles, QPV et proximité, EPFIF, "
    "socio-démographie communale."
)
LOGO_DOMAIN = "geoportail-urbanisme.gouv.fr"
