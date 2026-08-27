"""Déclaration de registre du connecteur `snitcher`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# snitcher : identification des visiteurs du site web — quelles ENTREPRISES
# visitent, avec sessions/événements par visite (dont les valeurs des
# formulaires soumis), contacts (reveal email = crédit payant), segments,
# tags et custom fields. keyed api_key (Bearer Personal Access Token,
# dashboard → Settings → Account → API), byo-only : un PAT est lié à UN
# compte Snitcher, une clé plateforme n'aurait pas de sens.
CONNECTOR = _c(
    "snitcher", ["snitcher"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Snitcher",
    help="identification des entreprises qui visitent votre site : "
         "organisations, sessions, contacts, segments, tags, custom fields",
    href="https://snitcher.com", credential_fields=(
        CredentialField("key", "Personal Access Token", secret=True,
                        help="Snitcher → Settings → Account → API → "
                             "Generate New Token"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Snitcher"
LOGO_DOMAIN = "snitcher.com"
