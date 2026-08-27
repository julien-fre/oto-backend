"""Déclaration de registre du connecteur `recruitee`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "recruitee", ["recruitee"], auth_modes={"byo_user"}, secret_kind="fields",
    label="Recruitee",
    help="ATS — candidats, offers (postes), notes",
    href="https://www.recruitee.com", credential_fields=(
        CredentialField("api_token", "API token", secret=True),
        CredentialField("company_id", "Company ID", secret=False),
    ),
)

CATEGORY = "Recrutement"
PUBLISHER = "Recruitee"
LOGO_DOMAIN = "recruitee.com"
