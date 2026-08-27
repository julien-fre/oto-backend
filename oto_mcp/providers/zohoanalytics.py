"""Déclaration de registre du connecteur `zohoanalytics`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "zohoanalytics", ["zohoanalytics"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Zoho Analytics",
    help="Zoho Analytics (workspaces, vues, export, requêtes SQL)",
    href="https://analytics.zoho.com", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True),
        CredentialField("client_secret", "Client Secret", secret=True),
        # FACULTATIF : rempli par le flux « se connecter avec Zoho » (server-based).
        CredentialField("refresh_token", "Refresh Token", secret=True,
                        required=False,
                        help="laisse vide si tu te connectes via Zoho"),
        CredentialField("org_id", "Org ID", secret=False),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca, sa)",
                        secret=False, reveal=True),
    ),
)

CATEGORY = "Knowledge"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"
