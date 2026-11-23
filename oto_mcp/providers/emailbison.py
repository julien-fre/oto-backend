"""Registry declaration of the `emailbison` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _decrit

# emailbison: cold email outreach on the customer's OWN EmailBison instance —
# campaigns and their stats, sequence steps, leads, the master inbox (replies and
# threads), sending accounts, tags, blacklists and webhooks. A neighbour of
# `instantly` and `lemlist` by trade. Served by the GENERIC READER of its
# description (`tools/decrits.py`): no `tools/emailbison.py`, the library's engine
# runs the description (auth `bearer`).
#
# TWO fields, read in the description: the API token (secret) and the address of
# the instance, the one the customer logs in to (a white-label domain or a host of
# EmailBison's), a `url` SETTING cited by `base_url` — hence `secret_kind="fields"`,
# both kept as one record and resolved by `access.resolve_credential_fields`. The
# engine guards that address at each request (https only, never a private host,
# no redirect followed). `byo_user` and `byo_org`, no platform key: the
# campaigns, leads and mailboxes are the customer's own.
#
# An `api-user` token stays in the workspace it was made in; a `super-admin` token
# follows whichever workspace its maker has switched to — the help asks for the
# first. The reference names no scope: a token reads and acts. What emails people
# (leads or a list into a campaign, resuming one, a reply) or posts to an outside
# address (a webhook) is `send`; deleting a webhook `destroy` (derived into
# `tools/effets.py`, never written there).
CONNECTOR = _decrit(
    "emailbison",
    help="cold email outreach on your EmailBison instance: campaigns and stats, leads, replies, webhooks",
    href="https://emailbison.com",
    key_help="EmailBison → Settings → Developer API → New API Token. Choose an api-user token: it stays "
             "in the workspace it is made in (a super-admin token follows its maker's current workspace). "
             "A token reads and acts.",
    settings_help={
        "instance_url": "The address you log in to EmailBison, without a trailing slash, e.g. "
                        "https://send.your-agency.com.",
    },
)

CATEGORY = "Outreach"
PUBLISHER = "EmailBison"
LOGO_DOMAIN = "emailbison.com"

DESCRIPTION = (
    "Cold email outreach with EmailBison, on your own instance: read campaigns and their stats, "
    "sequence steps, leads, the master inbox replies and threads, sending accounts and tags; "
    "create and tag leads, add leads or a list to a campaign, pause or resume it, reply to an email, "
    "mark a reply interested, blacklist an address or a domain, and register webhooks that wake "
    "agents on replies. An API token and the address you log in to."
)
