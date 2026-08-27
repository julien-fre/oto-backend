"""Déclaration de registre du connecteur `lemlist`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "lemlist", ["lemlist"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Lemlist", help="cold outreach", href="https://app.lemlist.com",
)

CATEGORY = "Prospection"
PUBLISHER = "lemlist"
LOGO_DOMAIN = "lemlist.com"
