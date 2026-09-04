"""Déclaration de registre du connecteur `lusha`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# lusha : recherche + reveal (emails/téléphones) de contacts, byo par défaut ;
# clé plateforme GRANT-ONLY depuis le 26/08 (#405). Auth = header `api_key`
# plat (pas OAuth), 1 seul
# endpoint câblé pour l'instant (search-and-enrich).
CONNECTOR = _c(
    "lusha", ["lusha"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Lusha", help="recherche + reveal de contacts (emails/téléphones)",
    publisher="Lusha", href="https://www.lusha.com",
)

CATEGORY = "Prospection"
LOGO_DOMAIN = "lusha.com"

DESCRIPTION = (
    "Recherche et reveal de contacts chez Lusha : retrouver l'email et le "
    "téléphone d'une personne à partir de son profil."
)
