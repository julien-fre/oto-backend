"""Déclaration de registre du connecteur `hunter`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "hunter", ["hunter"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=5, platform_key_open=True,
    label="Hunter.io", help="emails", href="https://hunter.io",
)

CATEGORY = "Prospection"
PUBLISHER = "Hunter.io"
LOGO_DOMAIN = "hunter.io"

DESCRIPTION = (
    "Retrouver et vérifier des adresses email professionnelles chez Hunter.io. "
    "Une clé plateforme gratuite est disponible, avec un quota limité par jour."
)
