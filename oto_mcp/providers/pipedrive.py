"""Déclaration de registre du connecteur `pipedrive`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# pipedrive : token API personnel (1 secret) + `company_domain` FACULTATIF non
# secret (route la requête vers le data center du compte — recommandé par
# Pipedrive pour la latence, jamais requis pour l'auth) → credential
# multi-champs (ADR 0011), resolve_credential_fields, pas keyed.
CONNECTOR = _c(
    "pipedrive", ["pipedrive"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Pipedrive",
    help="CRM (deals, personnes, organisations, activités, notes, leads)",
    href="https://app.pipedrive.com", credential_fields=(
        CredentialField("api_token", "Token API", secret=True,
                        help="Pipedrive → Paramètres personnels → API"),
        CredentialField("company_domain", "Sous-domaine du compte",
                        secret=False, required=False,
                        help="acme pour acme.pipedrive.com — facultatif"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Pipedrive"
LOGO_DOMAIN = "pipedrive.com"

DESCRIPTION = (
    "Le CRM Pipedrive : deals, personnes, organisations, activités, notes et "
    "leads. Le domaine de compte, facultatif, accélère les requêtes en les "
    "routant vers le bon data center."
)
