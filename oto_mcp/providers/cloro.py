"""Déclaration de registre du connecteur `cloro`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# cloro : veille AI-search (ChatGPT/Gemini/Perplexity/Copilot/Grok/AI Mode) +
# SERP Google en JSON. keyed api_key, byo par défaut ; mode plateforme
# GRANT-ONLY depuis le 26/08 (#405, GTM crédits) — renverse la décision produit
# des signaux #210-212 (« chaque org pose SA clé »), quota 0, jamais ouverte.
CONNECTOR = _c(
    "cloro", ["cloro"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Cloro",
    help="veille AI-search (ChatGPT, Gemini, Perplexity…) + SERP Google JSON",
    href="https://cloro.dev",
)

CATEGORY = "Prospection"
PUBLISHER = "Cloro"
LOGO_DOMAIN = "cloro.dev"
