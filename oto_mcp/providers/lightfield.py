"""Registry declaration of the `lightfield` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# lightfield: CRM whose field model is SPECIFIC TO EACH WORKSPACE (the
# keys are defined by the customer, not by the vendor) — hence an `op="definitions"`
# on each object, and key validation BEFORE writing. keyed api_key,
# **BYOK** (byo user/org): it is the customer's data, there cannot be
# a shared oto key. 29 granular scopes on the vendor side, chosen at key CREATION
# — the connection probe reads them and refuses a key without CRM read.
# ⚠️ Writes AND sends: `lightfield_emails(op="send")` goes out from a mailbox that the
# key owner has connected themselves, and is dry-run BY DEFAULT.
CONNECTOR = _c(
    "lightfield", ["lightfield"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Lightfield",
    help="CRM whose fields are specific to each workspace — accounts, "
         "contacts, opportunities, notes and email sending",
    href="https://lightfield.app",
)

CATEGORY = "CRM"
PUBLISHER = "Lightfield"
LOGO_DOMAIN = "lightfield.app"

DESCRIPTION = (
    "A CRM whose fields are specific to each workspace — the customer "
    "defines their own data model. The connector first reads the "
    "field definitions before writing, so it never guesses a key that "
    "does not exist for this customer. Can also send an email from a connected "
    "mailbox, dry-run by default."
)
