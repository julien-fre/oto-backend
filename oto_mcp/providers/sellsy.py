"""Déclaration de registre du connecteur `sellsy`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# sellsy : CRM + gestion commerciale FR (le CRM et la facturation dans le même
# compte). Credential = OAuth2 **client_credentials** (client_id + client_secret
# d'un accès « personnel » du portail développeur) → multi-champs, pas keyed :
# la clé d'appel est un jeton dérivé, pas le secret posé. byo-only — un compte
# Sellsy est celui d'une entreprise, il n'y a pas de clé plateforme à partager.
CONNECTOR = _c(
    "sellsy", ["sellsy"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Sellsy",
    help="CRM + gestion commerciale FR (tiers, opportunités, devis, factures, paiements)",
    href="https://www.sellsy.fr", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True,
                        help="Sellsy → Réglages → Portail développeur → API V2"),
        CredentialField("client_secret", "Client Secret", secret=True),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Sellsy"
LOGO_DOMAIN = "sellsy.com"
