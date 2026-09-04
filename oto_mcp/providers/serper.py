"""Déclaration de registre du connecteur `serper`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "serper", ["serper"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=200, platform_key_open=True,
    # Trois marques à un caractère près (serper/serpapi/searchapi) et quatre
    # périmètres voisins (cloro/firecrawl/tavily/brightdata) : chaque aide dit
    # depuis le 2026-09-02 CE QUI LA DISTINGUE des autres, pas ce qu'elle est.
    label="Serper",
    help="Google en JSON — web, images, Maps et avis, Lens, plus le scraping "
         "d'une page ; le moteur généraliste par défaut d'oto",
    href="https://serper.dev",
)

CATEGORY = "Prospection"
PUBLISHER = "Serper"
LOGO_DOMAIN = "serper.dev"

DESCRIPTION = (
    "Google en JSON — recherche web, images, Google Maps et ses avis, Lens, "
    "plus le scraping d'une page — le moteur de recherche généraliste par "
    "défaut d'oto. Clé plateforme partagée disponible, avec un quota quotidien."
)
