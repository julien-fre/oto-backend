"""Déclaration de registre du connecteur `hithorizons`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "hithorizons", ["hithorizons"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key", label="HitHorizons",
    help="données entreprise européennes (recherche + détails)",
    href="https://www.hithorizons.com",
)

CATEGORY = "Prospection"
PUBLISHER = "HitHorizons"
LOGO_DOMAIN = "hithorizons.com"
