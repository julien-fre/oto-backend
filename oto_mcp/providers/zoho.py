"""Registry declaration for the `zoho` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# zoho / zohodesk: OAuth2 self-client → multi-field credential (ADR 0011,
# like silae), resolved via resolve_credential_fields. byo_user OR byo_org
# (zoho: shareable org/group key — a sales team shares one self-client).
# `data_center` (non-secret) selects the Zoho region (com/eu/in…).
CONNECTOR = _c(
    "zoho", ["zoho"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Zoho CRM", account_noun="organisation",
    # No `cardinality`: `fields` derives it as multi since oto-backend#409.
    # Only the STATIC announcement of the axis remains curated — a Zoho user in
    # practice has several organizations, the axis is worth having in the schema upfront.
    account_axis_static=True,
    help="Zoho CRM (module CRUD, notes)", href="https://crm.zoho.com",
    credential_fields=(
        CredentialField("client_id", "Client ID", secret=True,
                        help="1000.XXXXXXXX… (self-client)"),
        CredentialField("client_secret", "Client Secret", secret=True,
                        help="self-client secret"),
        # OPTIONAL: in "connect with Zoho" mode (server-based) it is not
        # pasted — the consent flow fills it in. Required only when setting up
        # a self client by hand.
        CredentialField("refresh_token", "Refresh Token", secret=True,
                        required=False,
                        help="1000.xxxxx.yyyyy — leave empty if you connect via Zoho"),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca)",
                        secret=False, help="eu"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"

DESCRIPTION = (
    "The Zoho CRM: create, read, update modules and add notes. "
    "OAuth2 self-client, shareable by a whole team; the Zoho region "
    "(com/eu/in…) is chosen at connection time."
)
