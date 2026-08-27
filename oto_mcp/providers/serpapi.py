"""Déclaration de registre du connecteur `serpapi`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# serpapi : recherche multi-moteurs (scope complet — tous les verticaux Google
# + Bing/YouTube/Walmart/Amazon/eBay/… + Google Jobs). keyed api_key, platform-
# eligible (clé plateforme + quota daily, comme serper).
CONNECTOR = _c(
    "serpapi", ["serpapi"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=200, platform_key_open=True,
    label="SerpApi",
    help="recherche multi-moteurs (Google verticals, Bing, YouTube, Walmart, Amazon, jobs…)",
    href="https://serpapi.com",
)

CATEGORY = "Prospection"
PUBLISHER = "SerpApi"
LOGO_DOMAIN = "serpapi.com"
