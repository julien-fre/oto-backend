"""Registry declaration of the `pennylane_firm` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# pennylane_firm: Pennylane's "Firm" API, the ACCOUNTING FIRM side — one firm token
# reaches every company of the firm (portfolio, fiscal years, GED). DISTINCT from
# `pennylane` (Company API: one key = one company, company ids unrelated to these)
# and from `pennylaneged` (the GED through a browser session; same company ids as
# here, since both are the application's ids).
#
# The namespace carries the connector's name: `pennylane_firm_*` resolves to the
# longest DECLARED prefix (`namespace_of`), never to `pennylane` — same rule as
# `linkedin_unipile`.
#
# `byo_org` only: the token is the FIRM's, set by an org admin on the connector's
# card (org tier); it belongs to no person.
#
# ⚠️ Single-account, DECLARED: the derivation would yield `multi` (api_key). A
# provider and product reason, not a shape one: a firm token already reaches the
# whole portfolio, and the upload relay replays the sealed org without any `_account`
# axis (the URL is called by an anonymous `curl`). Two tokens in one org would be two
# firms that nothing could choose between.
CONNECTOR = _c(
    "pennylane_firm", ["pennylane_firm"], auth_modes={"byo_org"}, keyed=True,
    secret_kind="api_key", cardinality="mono",
    label="Pennylane (cabinet)",
    help="accounting firm: companies of the portfolio, their document store (GED)",
    href="https://app.pennylane.com",
    credential_fields=(
        CredentialField("key", "Firm API token", secret=True,
                        help="created by a firm admin in Pennylane (firm settings, "
                             "API); scopes companies:readonly and dms_files:readonly "
                             "to read, dms_files:all to create folders and upload"),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Pennylane"
LOGO_DOMAIN = "pennylane.com"

DESCRIPTION = (
    "An accounting firm's Pennylane, through its firm API token: the companies of "
    "the portfolio and each one's document store (GED) — browse folders and files, "
    "create a folder, upload a file. Not the `pennylane` "
    "connector, whose key belongs to one company."
)
