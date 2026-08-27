"""Déclaration de registre du connecteur `pennylane`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "pennylane", ["pennylane"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Pennylane", help="compta", href="https://app.pennylane.com",
)

CATEGORY = "Finance"
PUBLISHER = "Pennylane"
LOGO_DOMAIN = "pennylane.com"
