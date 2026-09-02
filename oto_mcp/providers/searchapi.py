"""Déclaration de registre du connecteur `searchapi`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# searchapi : recherche multi-moteurs via SearchApi.io (verticaux Google +
# YouTube/Bing/Amazon/… + jobs/news/maps/scholar). keyed api_key, platform-
# eligible (clé plateforme + quota daily, comme serper/serpapi). Client HTTP
# auto-contenu (pas de dép oto-core).
CONNECTOR = _c(
    "searchapi", ["searchapi"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=200, platform_key_open=True,
    label="SearchApi",
    help="même périmètre multi-moteurs que SerpApi (Google, YouTube, Bing, jobs, "
         "news, maps, scholar) — à poser si ta clé est chez SearchApi",
    href="https://www.searchapi.io",
)

CATEGORY = "Prospection"
PUBLISHER = "SearchApi"
LOGO_DOMAIN = "searchapi.io"
