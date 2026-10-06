"""Registry declaration of the `stripe` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# stripe: payments & billing — the customer's Stripe account.
# byo-only (no platform key): these are their account books, a key
# shared between orgs would make no sense.
# THREE fields rather than a bare `api_key`, because two NON-secret satellites
# decide WHAT the key reads: `api_version` (a pinned version that
# diverges from the account silently changes response shapes) and above all
# `stripe_account` — with Connect, the SAME question returns the revenue
# of a DIFFERENT company depending on this header. Making it a credential
# field is the strongest form of "never inferred per call": it
# is not a parameter of any tool, so it cannot be switched mid-conversation.
CONNECTOR = _c(
    "stripe", ["stripe"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Stripe",
    help="payments & billing — customers, subscriptions, invoices, payments, balance",
    href="https://stripe.com", credential_fields=(
        CredentialField("api_key", "Restricted key (rk_…) or secret key (sk_…)",
                        secret=True,
                        help="Stripe Dashboard → Developers → API keys → \"Create "
                             "restricted key\"; READ permissions are enough. "
                             "A publishable key `pk_…` is refused: it reads nothing."),
        CredentialField("api_version", "API version", secret=False,
                        required=False,
                        help="empty = the account's default version, the one its "
                             "dashboard shows"),
        CredentialField("stripe_account", "Connected account (optional, Connect)",
                        secret=False, required=False,
                        help="acct_… — ALL reads then apply to that "
                             "account, not to yours"),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Stripe"
LOGO_DOMAIN = "stripe.com"

DESCRIPTION = (
    "The payments and billing of a Stripe account: customers, subscriptions, "
    "invoices, payments and balance. Three credential fields: the secret "
    "key, an optional API version, and the Connect account identifier "
    "if you bill for several companies from a single account."
)
