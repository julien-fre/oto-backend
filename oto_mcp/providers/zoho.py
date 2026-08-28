"""Déclaration de registre du connecteur `zoho`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# zoho / zohodesk : OAuth2 self-client → credential multi-champs (ADR 0011,
# comme silae), résolu via resolve_credential_fields. byo_user OU byo_org
# (zoho : clé d'org/groupe partageable — équipe sales partage un self-client).
# `data_center` (non-secret) sélectionne la région Zoho (com/eu/in…).
CONNECTOR = _c(
    "zoho", ["zoho"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Zoho CRM", account_noun="organisation",
    # Pas de `cardinality` : `fields` la dérive en multi depuis oto-backend#409.
    # Seule l'annonce STATIQUE de l'axe reste curée — un utilisateur Zoho a en
    # pratique plusieurs organisations, l'axe vaut d'être au schéma d'emblée.
    account_axis_static=True,
    help="CRM Zoho (CRUD modules, notes)", href="https://crm.zoho.com",
    credential_fields=(
        CredentialField("client_id", "Client ID", secret=True,
                        help="1000.XXXXXXXX… (self-client)"),
        CredentialField("client_secret", "Client Secret", secret=True,
                        help="secret du self-client"),
        # FACULTATIF : en mode « se connecter avec Zoho » (server-based) il n'est
        # pas collé — le flux de consentement le remplit. Requis seulement si on
        # pose un self client à la main.
        CredentialField("refresh_token", "Refresh Token", secret=True,
                        required=False,
                        help="1000.xxxxx.yyyyy — laisse vide si tu te connectes via Zoho"),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca)",
                        secret=False, reveal=True, help="eu"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"
