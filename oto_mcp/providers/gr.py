"""Déclaration de registre du connecteur `gr`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# Grèce : lookup entité via registre GEMI (autocomplete) + VIES. Open data,
# sans clé. Inerte tant que non activé en DB (deny-by-default), comme foncier/sante.
CONNECTOR = _c(
    "gr", ["gr"], secret_kind="none",
    label="Data GR", help="entreprises Grèce — registre GEMI + VIES (open data)",
)

CATEGORY = "Data GR"
PUBLISHER = "GEMI / VIES"
SANS_LOGO_DE_MARQUE = True
