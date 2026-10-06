"""Registry declaration of the `silae` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# silae: French payroll. OAuth2 client-credentials auth (Azure AD B2C) = 3 secrets
# → generic multi-field model (ADR 0011). NOT keyed (resolved via
# access.resolve_credential_fields, no platform key or quota: byo-only,
# the credential IS the grant). Outside the base set → installable on demand
# (activation gate per org). IBAN/BIC masked before the agent (tools/silae.py).
CONNECTOR = _c(
    "silae", ["silae"], auth_modes={"byo_user"}, secret_kind="fields",
    label="Silae", help="French payroll (read) — Silae Paie API v1",
    href="https://www.silae.fr", credential_fields=(
        CredentialField("client_id", "Client ID", secret=True),
        CredentialField("client_secret", "Client Secret", secret=True),
        CredentialField("subscription_key", "Subscription Key", secret=True),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Silae"
LOGO_DOMAIN = "silae.fr"

DESCRIPTION = (
    "A company's payroll in Silae (read): payslips, employees, "
    "variable elements. IBAN and BIC are masked before reaching the agent. "
    "OAuth2 with three secrets, generated in the Silae admin."
)
