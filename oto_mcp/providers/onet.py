"""Déclaration de registre du connecteur `onet`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# onet : le référentiel des métiers du Department of Labor des États-Unis (O*NET
# Web Services v2) — recherche d'un métier, sa description, ses tâches, ses
# intitulés de poste. keyed api_key (en-tête `X-API-Key`), byo-only : la clé est
# gratuite mais nominative (inscription développeur, conditions d'usage acceptées
# par son titulaire), aucune clé plateforme n'est posée.
CONNECTOR = _c(
    "onet", ["onet"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="O*NET",
    help="référentiel des métiers aux États-Unis : trouver un code O*NET-SOC, "
         "lire la description d'un métier, ses tâches et ses intitulés de poste",
    href="https://services.onetcenter.org", credential_fields=(
        CredentialField("key", "Clé API", secret=True,
                        help="services.onetcenter.org → Sign up (gratuit) → "
                             "My Account → API keys"),
    ),
)

CATEGORY = "RH"
PUBLISHER = "O*NET (U.S. Department of Labor)"
LOGO_DOMAIN = "onetcenter.org"

DESCRIPTION = (
    "Le référentiel des métiers du Department of Labor des États-Unis : "
    "recherche par mot-clé ou par code, puis pour chaque métier sa description, "
    "ses tâches et les intitulés de poste réellement rencontrés. Le code "
    "O*NET-SOC trouvé ici donne le code SOC des statistiques de salaires."
)
