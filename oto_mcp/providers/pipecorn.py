"""Registry declaration of the `pipecorn` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# pipecorn: waterfall contact enrichment — a person's mobile phone, professional
# email or personal email, from a LinkedIn profile (or, for an email, a name and a
# company), Pipecorn running its own cascade of providers behind ONE synchronous call.
# The client lives in the connector library (`oto.tools.pipecorn.PipecornClient`),
# the tool in `tools/pipecorn.py` (one read tool, `pipecorn`: a lookup changes
# nothing at Pipecorn, it spends credits).
#
# ONE key in the `X-API-KEY` header (`keyed=True`, vault field `key`).
#
# BYO (user OR org: the customer's own Pipecorn credits) or a platform key on explicit
# grant (`platform_key_open=False`, `default_quota=0`: an org without a grant never
# reaches it). No per-org cap on the grant: the org's Tulina credits bound what it
# spends. The platform key serves a PHONE lookup only — the first step of
# `waterfall_phone`; emails, the personal email (its consent belongs to the key's owner)
# and the balance ask for the customer's own key. A lookup is ASYNCHRONOUS (submitted,
# then read back); the finished result notes the Pipecorn credits it spent
# (`quantity`, any key).
CONNECTOR = _c(
    "pipecorn", ["pipecorn"], auth_modes={"byo_user", "byo_org", "platform"},
    default_quota=0, platform_key_open=False,
    keyed=True, secret_kind="api_key",
    credential_fields=(
        CredentialField(
            "key", "API key", secret=True,
            help="Pipecorn → Settings → API keys. A test key (pc_test_…) answers "
                 "with sample data and spends nothing."),
    ),
    label="Pipecorn",
    help="waterfall contact enrichment: a person's mobile phone, work email or "
         "personal email from their LinkedIn profile; the shared key, when granted, "
         "covers phone lookups",
    href="https://www.pipecorn.com",
)

CATEGORY = "Prospecting"
PUBLISHER = "Pipecorn"
LOGO_DOMAIN = "pipecorn.com"

DESCRIPTION = (
    "Find a person's mobile phone, work email or personal email with Pipecorn, which "
    "queries many data providers one after the other in a single call. A LinkedIn "
    "profile is enough; an email can also be found from a name and a company. You "
    "only pay for what is found. Your own API key, or the shared key when it is "
    "granted to your org: it covers phone lookups; emails need your own key."
)
