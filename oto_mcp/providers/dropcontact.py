"""Déclaration de registre du connecteur `dropcontact`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# dropcontact : enrichissement contact + entreprise (email/téléphone/SIRENE) en
# batch async (submit/fetch, même idiome que fullenrich). byo par défaut ;
# clé plateforme GRANT-ONLY depuis le 26/08 (#405, GTM crédits — jamais ouverte).
CONNECTOR = _c(
    "dropcontact", ["dropcontact"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Dropcontact", help="enrichissement contact + entreprise (email/téléphone/SIRENE)",
    href="https://www.dropcontact.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Dropcontact"
LOGO_DOMAIN = "dropcontact.com"
