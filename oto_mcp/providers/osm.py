"""Déclaration de registre du connecteur `osm`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "osm", ["osm"], secret_kind="none",
    label="OpenStreetMap", help="points d'intérêt OSM par tag sur une zone (parkings, équipements, commerces) — recensement exhaustif via Overpass (open data)",
)

# Donnée publique tierce : l'éditeur retombait sur le défaut « Otomata » alors que
# la carte porte déjà le logo openstreetmap.org (corrigé le 2026-09-02).
PUBLISHER = "OpenStreetMap"
LOGO_DOMAIN = "openstreetmap.org"
