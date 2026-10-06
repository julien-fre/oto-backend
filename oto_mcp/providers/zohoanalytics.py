"""Registry declaration of the `zohoanalytics` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "zohoanalytics", ["zohoanalytics"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Zoho Analytics",
    help="Zoho Analytics (workspaces, views, export, SQL queries)",
    href="https://analytics.zoho.com", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True),
        CredentialField("client_secret", "Client Secret", secret=True),
        # OPTIONAL: filled in by the "connect with Zoho" flow (server-based).
        CredentialField("refresh_token", "Refresh Token", secret=True,
                        required=False,
                        help="leave empty if you connect via Zoho"),
        CredentialField("org_id", "Org ID", secret=False),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca, sa)",
                        secret=False),
    ),
)

CATEGORY = "Knowledge"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"

DESCRIPTION = (
    "Zoho Analytics dashboards: workspaces, views, data export "
    "and SQL queries on datasets. OAuth2 self-client, like the "
    "other Zoho connectors."
)
