"""Déclaration de registre du connecteur `scaleway`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# scaleway : email transactionnel via le compte Scaleway TEM DE L'ORG (BYO, comme resend).
# L'org amène sa clé (secret_key + project_id) ; l'API TEM n'envoie que depuis les domaines
# VÉRIFIÉS dans le compte Scaleway de l'org → propriété du domaine garantie par Scaleway,
# zéro logique domaine côté oto, plus d'override/activation (connecteur normal self-serve).
# Config (expéditeurs + fenêtre calme) dans le panneau email de la carte connecteur ORG ;
# email_send (spine) route sender→connecteur→transport.
CONNECTOR = _c(
    "scaleway", ["scaleway"], auth_modes={"byo_org"}, secret_kind="fields",
           label="Scaleway TEM (email)",
    help="envoi d'email transactionnel via ton compte Scaleway TEM (domaine vérifié chez Scaleway)",
    publisher="Scaleway", href="https://www.scaleway.com/en/transactional-email-tem/",
    credential_fields=(
        CredentialField("secret_key", "Clé secrète Scaleway (X-Auth-Token)", secret=True, reveal=True),
        CredentialField("project_id", "Project ID Scaleway", secret=False, reveal=True),
        CredentialField("region", "Région TEM (déf. fr-par)", secret=False, reveal=True),
    ),
)

LOGO_DOMAIN = "scaleway.com"
