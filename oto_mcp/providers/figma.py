"""Déclaration de registre du connecteur `figma`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "figma", ["figma"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Figma",
    help="fichiers, export d'images, commentaires, FigJam",
    href="https://www.figma.com",
)

CATEGORY = "Design"
PUBLISHER = "Figma"
LOGO_DOMAIN = "figma.com"

DESCRIPTION = (
    "Les fichiers Figma d'une équipe : lire leur contenu, exporter des images, "
    "consulter les commentaires, et FigJam."
)
