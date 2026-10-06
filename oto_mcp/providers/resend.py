"""Registry declaration of the `resend` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# resend: credential-only (NO tools of its own). The org's Resend key is
# consumed by `email_send` (transport=resend) via resolve_api_key, user >
# org cascade. Sending domain verified on the Resend side by the org; the `from` address
# lives in orgs.email_settings, not in the credential. Outside the base (not a
# tool to expose). tools/resend.py = no-op register() to
# satisfy the invariant "one tools/ file per provider kind=tools".
# resend: BYOK transactional email (the ORG's Resend key). byo_org only
# (email is org-level); self_serve = available on request to any org. Domain
# ownership is guaranteed by Resend (the key can only send from the
# domains verified in the org's Resend account) → zero domain logic on the oto side.
CONNECTOR = _c(
    "resend", ["resend"], auth_modes={"byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Resend", help="transactional email sending (the org's key)",
    publisher="Resend", href="https://resend.com",
)

LOGO_DOMAIN = "resend.com"

DESCRIPTION = (
    "Transactional email sending through your organization's Resend key — no "
    "tools of its own: the key is consumed by the platform's email "
    "sending, from a domain you have verified at Resend."
)
