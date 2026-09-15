"""Déclaration de registre du connecteur `lucca`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# lucca : RH FR (annuaire, absences, notes de frais, organisation), API v3
# "legacy". Auth = clé API statique en HEADER (PAS OAuth2, PAS Bearer —
# "Authorization: lucca application={key}") + un domaine de tenant, deux
# secrets → modèle générique multi-champs (ADR 0011), même famille que silae.
# PAS keyed (byo-only : le credential EST le grant, pas de clé plateforme ni
# de quota — chaque cabinet/employeur a son propre compte Lucca). Hors socle
# → installable à la demande (cran d'activation par org).
CONNECTOR = _c(
    "lucca", ["lucca"], auth_modes={"byo_user"}, secret_kind="fields",
    label="Lucca", help="RH FR (lecture) — annuaire, absences, frais, organisation",
    href="https://www.lucca.fr", credential_fields=(
        CredentialField(
            "api_key", "Clé API", secret=True,
            help="Compte Lucca → Réglages → API → générer une clé d'application."),
        CredentialField(
            "domain", "Sous-domaine", secret=False,
            help="Le sous-domaine SEUL de ton instance Lucca — ex. « acme » pour "
                 "acme.ilucca.net (pas l'URL complète)."),
    ),
)

CATEGORY = "RH"
PUBLISHER = "Lucca"
LOGO_DOMAIN = "lucca.fr"

DESCRIPTION = (
    "L'annuaire, les absences, les notes de frais et l'organisation d'une "
    "entreprise dans Lucca (lecture). Clé API à générer côté admin Lucca, "
    "propre au sous-domaine de l'instance."
)
