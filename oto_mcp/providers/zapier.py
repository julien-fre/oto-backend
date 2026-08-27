"""Déclaration de registre du connecteur `zapier`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "zapier", ["zapier"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Zapier",
    help="automatisation — actions exposées (AI Actions) + exécution",
    href="https://actions.zapier.com",
)

CATEGORY = "Automatisation"
PUBLISHER = "Zapier"
LOGO_DOMAIN = "zapier.com"
