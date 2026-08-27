"""Déclaration de registre du connecteur `tavily`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# tavily : recherche web + extract/crawl/map « pour agent » (réponse sourcée en
# un appel). keyed api_key ; byo user/org ET clé plateforme OUVERTE (socle de
# recherche web, pas de ticket d'entrée). ⚠️ quota 100/mois depuis le 26/08 :
# la PR #407 posait 0, qui n'est PAS « petit » mais ILLIMITÉ (0 falsy dans
# access/quotas.py) — or un crawl coûte jusqu'à 20 crédits l'appel. 100 = garde
# conservatrice type serper (200), réversible en un chiffre.
CONNECTOR = _c(
    "tavily", ["tavily"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=100, platform_key_open=True,
    label="Tavily",
    help="recherche web pour agent (réponse sourcée), extract, crawl et map de site",
    href="https://app.tavily.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Tavily"
LOGO_DOMAIN = "tavily.com"
