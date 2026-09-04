"""Déclaration de registre du connecteur `apify`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# apify : catalogue d'« actors » (scrapers hébergés prêts à l'emploi — Google
# Maps, LinkedIn, Amazon…) qu'on lance avec un JSON d'entrée et dont on lit le
# dataset. keyed api_key, byo par défaut (un run se facture à l'usage sur le
# compte de l'org) ; clé plateforme GRANT-ONLY depuis le 26/08 (#405).
CONNECTOR = _c(
    "apify", ["apify"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Apify",
    help="scrapers hébergés prêts à l'emploi (Google Maps, LinkedIn, Amazon…) via le Store",
    href="https://apify.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Apify"
LOGO_DOMAIN = "apify.com"

DESCRIPTION = (
    "Le Store d'Apify : lancer un scraper hébergé prêt à l'emploi (Google Maps, "
    "LinkedIn, Amazon…) avec un JSON d'entrée, puis lire son résultat "
    "(dataset). Accès plateforme réservé (grant explicite) ; chaque run se "
    "facture à l'usage sur le compte connecté."
)
