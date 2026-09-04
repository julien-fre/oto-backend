"""Déclaration de registre du connecteur `tally`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# tally : formulaires en ligne — formulaires, questions, blocs, réponses,
# analytics, espaces de travail, dossiers, membres de l'organisation, webhooks
# (38 opérations, couverture complète de l'API publique). keyed api_key
# (Bearer `tly-…`), **byo-only et byo-only par nature** : une clé Tally est liée
# à UN utilisateur, hérite de ses droits (aucun scope fin n'existe côté Tally)
# et cesse de fonctionner s'il quitte l'organisation — une clé plateforme
# partagée serait donc un compte nominatif déguisé, pas une clé de service.
CONNECTOR = _c(
    "tally", ["tally"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Tally",
    help="formulaires : réponses, questions, blocs, analytics, espaces, webhooks",
    href="https://tally.so",
)

CATEGORY = "Métier"
PUBLISHER = "Tally"
LOGO_DOMAIN = "tally.so"

DESCRIPTION = (
    "Les formulaires en ligne créés avec Tally : réponses, questions, blocs, "
    "analytics, espaces de travail, dossiers, membres de l'organisation et "
    "webhooks. Une clé Tally est nominative — elle hérite des droits de la "
    "personne qui l'a créée, et cesse de fonctionner si elle quitte "
    "l'organisation."
)
