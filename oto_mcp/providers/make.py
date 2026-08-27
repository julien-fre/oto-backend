"""Déclaration de registre du connecteur `make`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "make", ["make"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Make",
    help="automatisation de workflows — scénarios, exécution, logs (API v2)",
    href="https://www.make.com", credential_fields=(
        CredentialField("api_token", "API token", secret=True),
        CredentialField("base_url", "Zone URL", secret=False,
                        help="ex. https://eu1.make.com ou https://us1.make.com"),
    ),
)

CATEGORY = "Automatisation"
PUBLISHER = "Make"
LOGO_DOMAIN = "make.com"
