"""Registry declaration of the `typeform` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# typeform: online forms, READ ONLY — workspaces,
# forms, a form's definition, responses. No writes, no
# webhooks. byo (user OR org), resolved via `resolve_credential_fields`:
# a personal token acts in the name of its creator and carries its scopes, no
# shared platform key.
# TWO fields: the token, and the account's region. Typeform has three hosts (one
# per data center) and an account's RESPONSES can only be read in its own:
# elsewhere, the list comes back empty without error. The region is DECLARED at setup,
# closed set (`choices`) — never a free URL, hence no typed-in destination.
CONNECTOR = _c(
    "typeform", ["typeform"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Typeform",
    help="online forms, read only: workspaces, forms and their questions, responses",
    href="https://www.typeform.com",
    credential_fields=(
        CredentialField(
            "key", "Typeform personal access token", secret=True,
            help="Typeform → Account → Personal tokens → Generate a new token "
                 "(`tfp_…`). Scopes: forms:read, responses:read, "
                 "workspaces:read. The token acts as the account that created it."),
        CredentialField(
            "region", "Data center", secret=False, required=False,
            choices=("us", "eu", "eu2"),
            help="Where the account's responses are stored: empty or « us » "
                 "(api.typeform.com), « eu » (api.eu.typeform.com), « eu2 » "
                 "(api.typeform.eu, the newer EU data center). Responses read "
                 "from another data center come back empty."),
    ),
)

CATEGORY = "Business apps"
PUBLISHER = "Typeform"
LOGO_DOMAIN = "typeform.com"

DESCRIPTION = (
    "Online forms built with Typeform, read only: workspaces, forms with their "
    "questions and choices, and responses filtered by date, completion or text. "
    "Each person or organization connects its own personal access token, with "
    "the account's data center (US or EU) — no shared platform key."
)
