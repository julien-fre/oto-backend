"""Déclaration de registre du connecteur `zohodesk`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "zohodesk", ["zohodesk"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Zoho Desk",
    help="support Zoho Desk (tickets, threads, contacts, articles KB)",
    href="https://desk.zoho.com", credential_fields=(
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
        # FACULTATIF : les endpoints KB (articles) résolvent le portail depuis le
        # token mono-org — vérifié empiriquement. Et un credential scopé
        # `Desk.articles.READ` seul ne PEUT pas le découvrir (/organizations →
        # 403 SCOPE_MISMATCH), donc l'exiger rendait le connecteur impossible à
        # poser pour ce cas. Reste utile aux endpoints qui réclament l'en-tête
        # `orgId` (tickets…), qui l'exigeront alors côté API.
        CredentialField("org_id", "Org ID (facultatif)", secret=False,
                        required=False,
                        help="ex. 800123456 — inutile pour lire les articles"),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca)",
                        secret=False, help="eu"),
    ),
)

CATEGORY = "Comms"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"
