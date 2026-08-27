"""Déclaration de registre du connecteur `serper`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "serper", ["serper"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=200, platform_key_open=True,
    label="Serper", help="recherche web", href="https://serper.dev",
)

CATEGORY = "Prospection"
PUBLISHER = "Serper"
LOGO_DOMAIN = "serper.dev"
