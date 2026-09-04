"""Déclaration de registre du connecteur `phantombuster`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "phantombuster", ["phantombuster"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Phantombuster",
    help="agents d'automatisation (launch + résultats)",
    href="https://phantombuster.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Phantombuster"
LOGO_DOMAIN = "phantombuster.com"

DESCRIPTION = (
    "Les agents d'automatisation Phantombuster : lancer un agent et lire ses "
    "résultats."
)
