"""Déclaration de registre du connecteur `frenchtech`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "frenchtech", ["frenchtech"], secret_kind="none",
    label="French Tech", help="annuaire écosystème d'une capitale French Tech (startups/structures/prestataires) + événements, appels à projet, financements + French Tech Central (open data, défaut Aix-Marseille)",
)

CATEGORY = "Data FR"
PUBLISHER = "La French Tech (open data)"
DESCRIPTION = (
    "L'écosystème d'une capitale French Tech (défaut Aix-Marseille) : "
    "annuaire des startups, structures et prestataires, événements, "
    "appels à projets, financements et French Tech Central."
)
LOGO_DOMAIN = "lafrenchtech.com"
