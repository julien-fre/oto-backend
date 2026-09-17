"""Déclaration de registre du connecteur `inqom`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# inqom : production comptable FR (cabinets et PME). Auth OAuth2 Resource Owner
# (`grant_type=password`) = clés d'application (client_id/client_secret, fournies
# par Inqom) + identifiants du compte Inqom au nom duquel le jeton agit (en
# pratique un compte système non nominatif du cabinet) → modèle générique
# multi-champs (ADR 0011). PAS keyed : byo-only, le credential EST le grant. Hôte
# fixe (api.inqom.com) : aucun champ ne désigne une destination, pas de garde
# d'egress à poser. Hors socle → installable à la demande.
CONNECTOR = _c(
    "inqom", ["inqom"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Inqom", help="compta FR — dossiers, plan comptable, balance, écritures",
    href="https://www.inqom.com", credential_fields=(
        CredentialField(
            "client_id", "Client ID", secret=True,
            help="Clé d'application API fournie par Inqom."),
        CredentialField(
            "client_secret", "Client Secret", secret=True,
            help="Secret de l'application API fourni par Inqom avec le Client ID."),
        CredentialField(
            "username", "Identifiant du compte Inqom", secret=False,
            help="Le compte au nom duquel l'API agit — de préférence un compte "
                 "système du cabinet, dont les droits bornent ce qui est visible."),
        CredentialField(
            "password", "Mot de passe du compte Inqom", secret=True,
            whitespace_significant=True),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Inqom"
LOGO_DOMAIN = "inqom.com"

DESCRIPTION = (
    "La production comptable d'un cabinet ou d'une PME dans Inqom : dossiers, "
    "exercices, plan comptable et comptes de tiers, journaux, balance, lignes "
    "d'écriture et pièces, plus la saisie d'écritures avec aperçu préalable. "
    "Clés d'application fournies par Inqom et compte Inqom dédié : ce compte "
    "borne ce qui est visible."
)
