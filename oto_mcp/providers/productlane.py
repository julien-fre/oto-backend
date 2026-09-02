"""Déclaration de registre du connecteur `productlane`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# productlane : retours clients (fils, contacts, entreprises), roadmap publique
# et centre d'aide. Voisin de `linear` — même catégorie Métier — et ce n'est pas
# qu'une parenté thématique : **la roadmap de Productlane EST adossée à Linear**.
# Projets et issues y sont créés dans Linear d'abord, puis reflétés ici. Une org
# qui a les deux connecteurs voit donc les mêmes objets par deux portes, et c'est
# normal.
#
# ⚠️ API **v2** (`/api/v2`, Bearer). Une clé v1 ne marche pas : v1 est une API
# distincte, qui s'arrête le 2026-11-20.
#
# BYO org d'abord (les retours clients sont ceux de l'organisation, pas d'une
# personne), mais `byo_user` reste ouvert : la clé se crée par membre côté
# Productlane, et une org qui débute pose souvent celle de son PM avant d'en
# faire une clé d'équipe. Aucun mode plateforme — ce sont les conversations
# clients de l'org.
CONNECTOR = _c(
    "productlane", ["productlane"], auth_modes={"byo_user", "byo_org"},
    keyed=True, secret_kind="api_key",
    label="Productlane",
    help="retours clients (fils, contacts, entreprises), roadmap adossée à "
         "Linear, changelogs et centre d'aide",
    href="https://productlane.com",
)

CATEGORY = "Métier"
PUBLISHER = "Productlane"
LOGO_DOMAIN = "productlane.com"
