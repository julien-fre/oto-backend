"""Déclaration de registre du connecteur `theirstack`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# theirstack : offres d'emploi par employeur + technologies détectées dans les
# offres (technographie : ERP, CRM…). Deux endpoints POST dont le corps est la
# DSL de filtres éditeur, passée telle quelle. keyed api_key (Bearer), byo par
# défaut — facturation au crédit, au record ENTREPRISE rendu ; clé plateforme
# GRANT-ONLY depuis le 26/08 (#405). Couverture PME partielle : `data: []` est normal.
# Lecture seule.
CONNECTOR = _c(
    "theirstack", ["theirstack"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="TheirStack",
    help="offres d'emploi par employeur + technologies utilisées (ERP…)",
    href="https://theirstack.com",
)

CATEGORY = "Prospection"
PUBLISHER = "TheirStack"
LOGO_DOMAIN = "theirstack.com"
