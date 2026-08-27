"""Déclaration de registre du connecteur `ahrefs`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# ahrefs : SEO — backlinks, mots-clés, rank tracking, audits techniques,
# visibilité de marque sur les chatbots IA, analytics on-site, GSC, social
# publishing. keyed api_key (Bearer), byo-only (pas de clé plateforme) :
# un seat Ahrefs est cher et par abonnement.
CONNECTOR = _c(
    "ahrefs", ["ahrefs"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Ahrefs",
    help="SEO — backlinks, mots-clés, rank tracking, audits techniques, "
         "visibilité de marque sur les chatbots IA, analytics, GSC, social",
    href="https://ahrefs.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Ahrefs"
LOGO_DOMAIN = "ahrefs.com"
