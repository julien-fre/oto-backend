"""Registry declaration of the `apify` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# apify: catalog of "actors" (ready-to-use hosted scrapers — Google
# Maps, LinkedIn, Amazon…) launched with a JSON input and whose dataset is
# read. keyed api_key, byo by default (a run is billed by usage on the
# org's account); platform key GRANT-ONLY since 26/08 (#405).
CONNECTOR = _c(
    "apify", ["apify"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # platform key on explicit grant (data bought by credit)
    secret_kind="api_key",
    label="Apify",
    help="ready-to-use hosted scrapers (Google Maps, LinkedIn, Amazon…) via the Store",
    href="https://apify.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Apify"
LOGO_DOMAIN = "apify.com"

DESCRIPTION = (
    "The Apify Store: launch a ready-to-use hosted scraper (Google Maps, "
    "LinkedIn, Amazon…) with a JSON input, then read its result "
    "(dataset). Platform access is restricted (explicit grant); each run is "
    "billed by usage on the connected account."
)
