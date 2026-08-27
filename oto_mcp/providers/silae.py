"""Déclaration de registre du connecteur `silae`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# silae : paie FR. Auth OAuth2 client-credentials (Azure AD B2C) = 3 secrets
# → modèle générique multi-champs (ADR 0011). PAS keyed (résolu via
# access.resolve_credential_fields, pas de clé plateforme ni quota : byo-only,
# le credential EST le grant). Hors socle → installable à la demande
# (cran d'activation par org). IBAN/BIC masqués avant l'agent (tools/silae.py).
CONNECTOR = _c(
    "silae", ["silae"], auth_modes={"byo_user"}, secret_kind="fields",
    label="Silae", help="paie FR (lecture) — API Silae Paie v1",
    href="https://www.silae.fr", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True),
        CredentialField("client_secret", "Client Secret", secret=True),
        CredentialField("subscription_key", "Subscription Key", secret=True),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Silae"
LOGO_DOMAIN = "silae.fr"
