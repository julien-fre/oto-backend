"""Registry declaration of the `zohodesk` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

CONNECTOR = _c(
    "zohodesk", ["zohodesk"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Zoho Desk",
    help="Zoho Desk support (tickets, threads, contacts, KB articles)",
    href="https://desk.zoho.com", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True,
                        help="1000.XXXXXXXX… (self-client)"),
        CredentialField("client_secret", "Client Secret", secret=True,
                        help="self-client secret"),
        # OPTIONAL: in "sign in with Zoho" mode (server-based) it is not
        # pasted — the consent flow fills it in. Required only when a self client
        # is set up by hand.
        CredentialField("refresh_token", "Refresh Token", secret=True,
                        required=False,
                        help="1000.xxxxx.yyyyy — leave empty if you sign in via Zoho"),
        # OPTIONAL: the KB endpoints (articles) resolve the portal from the
        # single-org token — verified empirically. And a credential scoped to
        # `Desk.articles.READ` alone CANNOT discover it (/organizations →
        # 403 SCOPE_MISMATCH), so requiring it made the connector impossible to
        # set up for that case. Still useful for endpoints that require the
        # `orgId` header (tickets…), which will then demand it on the API side.
        CredentialField("org_id", "Org ID (optional)", secret=False,
                        required=False,
                        help="e.g. 800123456 — not needed to read articles"),
        CredentialField("data_center", "Data center (com, eu, in, au, jp, ca)",
                        secret=False, help="eu"),
    ),
)

CATEGORY = "Comms"
PUBLISHER = "Zoho"
LOGO_DOMAIN = "zoho.com"

DESCRIPTION = (
    "Customer support in Zoho Desk: tickets, conversation threads, contacts "
    "and knowledge-base articles."
)
