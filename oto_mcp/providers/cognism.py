"""Déclaration de registre du connecteur `cognism`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# cognism : connecteur classique (kind="tools") sur l'API Search de Cognism
# (developers.cognism.com). Client REST synchrone dans oto-core
# (`oto.tools.cognism`), tools curés dans `tools/cognism.py`. Cascade de clé
# standard (`resolve_api_key`) — BYO org couvre le besoin "une clé pour tout
# l'org" ; mode "platform" GRANT-ONLY depuis le 26/08 (#405, GTM crédits —
# ce doc disait l'inverse jusque-là, faute d'accord commercial Otomata↔Cognism).
# search_contacts/search_accounts = preview only (flags `has*`, pas
# d'email/téléphone réel) ; redeem_contacts/redeem_accounts = reveal complet
# (consomme des crédits) ; enrich_contact/enrich_account = lookup par
# identité (email/LinkedIn/nom+société). DSL de filtre (~150 champs)
# documentée dans le guide `cognism-filters`, pas dans les docstrings tool.
CONNECTOR = _c(
    "cognism", ["cognism"],
    auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Cognism",
    help="B2B contact & company search, reveal, and identity enrichment",
    href="https://cognism.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Cognism"
LOGO_DOMAIN = "cognism.com"

DESCRIPTION = (
    "Recherche de contacts et d'entreprises B2B chez Cognism, avec reveal "
    "(email, téléphone) au crédit et enrichissement par identité (email, "
    "LinkedIn, nom + société). Les recherches restent en aperçu tant que le "
    "reveal n'est pas demandé explicitement."
)
