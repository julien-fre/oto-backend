"""Déclaration de registre du connecteur `sirene`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# `fr` (APIs live SIRENE/Recherche Entreprises/INPI/BODACC/BOAMP) + `fr_groupe`
# (chaîne capitalistique : mandataires personnes morales du RNE, #337) + `fr_stock`
# (stock SIRENE parquet, ex-connecteur `sirene_stock`, fusionné 2026-06-22 :
# même domaine entreprises FR, namespace fr_stock_* → namespace_of="fr").
# default_quota=0 (illimité) : données entreprise FR ouvertes à tous, sans
# crédits. La plupart des fr_* sont open-data/parquet (aucune clé) ; seuls
# fr_siret/fr_avis_sirene/fr_headquarters touchent la clé INSEE partagée —
# non métrée. Le seul plafond restant = le rate limit INSEE (30 req/min) sur
# la clé partagée, remonté tel quel (429) sans throttle oto.
CONNECTOR = _c(
    "sirene", ["fr"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=0, platform_key_open=True,
    label="INSEE SIRENE", help="données entreprise FR",
    href="https://api.insee.fr", modules=("fr", "fr_stock", "fr_groupe"),
)

CATEGORY = "Data FR"
PUBLISHER = "INSEE"
DESCRIPTION = (
    "Les données d'entreprise françaises unifiées : recherche "
    "multicritère, fiche agrégée (identité + bilans INPI + événements "
    "BODACC), dirigeants, marchés publics BOAMP, accords "
    "d'entreprise. Inclut le stock SIRENE complet (~43 M "
    "d'établissements) pour le batch : sièges, établissements, "
    "recherche NAF/commune."
)
LOGO_DOMAIN = "insee.fr"
