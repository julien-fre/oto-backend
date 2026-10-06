"""Registry declaration of the `gocardless` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# keyed BYO (user OR org), resolved via resolve_api_key like pennylane/attio.
# self_serve: everyone connects THEIR OWN GoCardless account (sandbox or prod) —
# NO shared platform key, so nothing sensitive to gate by grant. Stays
# outside the foundation → opt-in, not imposed. A client org
# sets its service account's token there for the credit-note POC (a client org's guide).
CONNECTOR = _c(
    "gocardless", ["gocardless"], availability="self_serve",
    auth_modes={"byo_user", "byo_org"}, keyed=True, secret_kind="api_key",
    label="GoCardless", help="SEPA direct debits (read)",
)

CATEGORY = "Finance"
PUBLISHER = "GoCardless"
LOGO_DOMAIN = "gocardless.com"

DESCRIPTION = (
    "The SEPA direct debits of a GoCardless account, read-only. Everyone connects "
    "their own account (sandbox or production) — no shared platform "
    "key."
)
