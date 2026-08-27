"""Déclaration de registre du connecteur `firecrawl`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# firecrawl : une URL → markdown propre (rendu JS, nav retirée) ; map/crawl pour
# un site entier, search pour du web + contenu. keyed api_key, byo par défaut
# (facturation au crédit chez l'éditeur) ; clé plateforme GRANT-ONLY depuis le
# 26/08 (#405, GTM crédits).
CONNECTOR = _c(
    "firecrawl", ["firecrawl"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Firecrawl",
    help="pages web en markdown propre — scrape, crawl d'un site, map, search",
    href="https://firecrawl.dev",
)

CATEGORY = "Prospection"
PUBLISHER = "Firecrawl"
LOGO_DOMAIN = "firecrawl.dev"
