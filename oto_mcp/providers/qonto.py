"""Registry declaration of the `qonto` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# qonto: French business banking (Business API v2) — the organization's bank
# accounts and balances, transactions with VAT and receipts, statements,
# supplier invoices, SEPA transfers and beneficiaries, spend requests, and the
# invoicing side (client invoices, credit notes, quotes, clients). The client
# lives in the connector library (`oto.tools.qonto.QontoClient`), the tool in
# `tools/qonto.py` (one read-only tool, the read verb in `op`).
#
# ONE field: Qonto's API key is a login and a secret key that it wants joined by
# a colon in `Authorization`, sent as is (no Basic, no base64). The connector
# description (`connectors/qonto/connector.yaml` in oto-core) says it as one
# credential field, so the vault keeps one too.
#
# Strict BYOK (`byo_user` + `byo_org`, no platform mode): the customer's own bank
# account. Read-only: an API key reaches no payment route (Qonto keeps those
# behind an OAuth consent), and cards and cash-flow categories are OAuth only.
CONNECTOR = _c(
    "qonto", ["qonto"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Qonto",
    help="business banking, read-only: balances, transactions, receipts, statements, "
         "supplier and client invoices",
    href="https://qonto.com",
    credential_fields=(
        CredentialField(
            "key", "API key (login:secret-key)", secret=True,
            help="Qonto → Settings → Integrations and partnerships → API key. Copy the "
                 "login (like acme-1234, not your email) and the secret key, and paste "
                 "them joined by a colon: login:secret-key. Only an owner or admin "
                 "sees this page."),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Qonto"
LOGO_DOMAIN = "qonto.com"

DESCRIPTION = (
    "Your Qonto business account, read-only: balances of every bank account, "
    "transactions with their VAT and receipts, monthly statements, supplier "
    "invoices, SEPA transfers and beneficiaries, spend requests, and the invoices, "
    "credit notes and quotes issued from Qonto. Your organization's API key."
)
