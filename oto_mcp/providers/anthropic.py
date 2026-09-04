"""Déclaration de registre du porteur de clé `anthropic`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# ⚠️ `kind="credential"` : AUCUN outil. Cet objet ne sert qu'à porter la clé
# que la plateforme utilise POUR LE COMPTE de l'org — un agent programmé la
# consomme, aucun tool ne l'expose. Un connecteur ordinaire à namespaces vides
# se présenterait comme un connecteur sans en avoir les effets ; le type
# distinct laisse l'écran dire ce que c'est.
#
# `byo_org` seul : la clé est celle de l'ORGANISATION, pas d'une personne —
# c'est elle qui paie les tours, et un agent programmé survit à son auteur.
CONNECTOR = _c(
    "anthropic", [], kind="credential", auth_modes={"byo_org"}, keyed=True,
    # ⚠️ MONO-compte, déclaré : la dérivation rendrait `multi` (api_key), et
    # l'écran proposerait de poser une deuxième clé que rien ne saurait choisir.
    # Un passage tourne sur UNE clé — deux dépôts pour la même org, ce serait deux
    # factures pour un même travail, et le worker n'a aucun critère pour trancher.
    cardinality="mono",
    secret_kind="api_key", label="Anthropic",
    help="Clé de modèle Anthropic — utilisée par les agents programmés de l'organisation, jamais par un outil",
    href="https://console.anthropic.com/settings/keys",
)

CATEGORY = "Modèles"
PUBLISHER = "Anthropic"
LOGO_DOMAIN = "anthropic.com"
