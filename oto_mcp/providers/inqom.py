"""Registry declaration of the `inqom` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# inqom: French accounting production (firms and SMEs). OAuth2 Resource Owner auth
# (`grant_type=password`) = application keys (client_id/client_secret, provided
# by Inqom) + credentials of the Inqom account on whose behalf the token acts (in
# practice a non-personal system account of the firm) → generic multi-field
# model (ADR 0011). NOT keyed: byo-only, the credential IS the grant. Fixed
# host (api.inqom.com): no field designates a destination, no egress guard
# to set. Outside the core set → installable on demand. READ-ONLY: no
# write is wired (30/09/2026, like payfit) — `inqom_entry_create` returns
# `inqom_write_not_wired` (see `tools/ecriture_non_cablee.py`).
CONNECTOR = _c(
    "inqom", ["inqom"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Inqom", help="French accounting, read-only: files, chart of accounts, balance, entries",
    href="https://www.inqom.com", credential_fields=(
        CredentialField(
            "client_id", "Client ID", secret=True,
            help="API application key provided by Inqom."),
        CredentialField(
            "client_secret", "Client Secret", secret=True,
            help="API application secret provided by Inqom with the Client ID."),
        CredentialField(
            "username", "Inqom account username", secret=False,
            help="The account on whose behalf the API acts — preferably a "
                 "system account of the firm, whose rights bound what is visible."),
        CredentialField(
            "password", "Inqom account password", secret=True,
            whitespace_significant=True),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Inqom"
LOGO_DOMAIN = "inqom.com"

DESCRIPTION = (
    "The accounting production of a firm or SME in Inqom: files, "
    "fiscal years, chart of accounts and third-party accounts, journals, balance, "
    "entry lines (by account or by prefixes, with the third party of each entry) and "
    "documents. Read-only: the connector never writes "
    "to Inqom. Application keys provided by Inqom and a dedicated Inqom account: this "
    "account bounds what is visible."
)
