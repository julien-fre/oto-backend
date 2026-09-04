"""Déclaration de registre du connecteur `brightdata`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# brightdata : scraping & SERP via réseau proxy Bright Data. COQUILLE VIDE —
# connecteur câblé (clé platform + quota) mais produits (SERP/Unlocker/Datasets)
# pas encore implémentés (tools/brightdata.py n'expose aucun tool pour l'instant).
CONNECTOR = _c(
    "brightdata", ["brightdata"], auth_modes={"byo_user", "byo_org", "platform"},
    keyed=True, secret_kind="api_key",
    default_quota=50, label="Bright Data",
    help="scraping & SERP via proxy (coquille vide — à implémenter)",
    href="https://brightdata.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Bright Data"
LOGO_DOMAIN = "brightdata.com"

DESCRIPTION = (
    "Scraping et SERP via le réseau de proxys Bright Data — la carte existe, "
    "les tools (SERP, Unlocker, Datasets) ne sont pas encore construits."
)
