"""Registry declaration of the `snowflake` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _decrit

# snowflake: read-only SQL on the customer's OWN Snowflake account (SQL API v2) —
# SELECT / WITH / SHOW / DESCRIBE / EXPLAIN, one statement per call, values in
# bindings; a statement still running after the API's 45 s wait, or a further
# partition of a result, is read by its handle. A neighbour of `bigquery` by trade
# (data warehouse). Served by the GENERIC READER of its description
# (`tools/decrits.py`): no `tools/snowflake.py`, the library's engine runs the
# description (auth `bearer`, the token-type header a constant of the description).
#
# TWO fields, read in the description: the programmatic access token (secret) and
# the account identifier (`myorg-myaccount`), a `text` SETTING cited by `base_url`
# — hence `secret_kind="fields"`, both kept as one record and resolved by
# `access.resolve_credential_fields`. `byo_user` and `byo_org`, no platform key:
# the data is the customer's own.
#
# ⚠️ The SQL API has NO read-only mode: the input pattern refuses what does not
# open like a read, but the real boundary is the token's role. The help asks for a
# token made with ROLE_RESTRICTION on a role that only has USAGE on a warehouse and
# SELECT on what the agent may read. A PAT is refused (401) when its user is under
# no network policy allowing our egress addresses: the help says it.
#
# Read only: every function is `read`, so the reader serves `snowflake` alone,
# never a `snowflake_act`. A query bills the customer's warehouse credits.
CONNECTOR = _decrit(
    "snowflake",
    help="read-only SQL on your Snowflake account: queries, SHOW and DESCRIBE, long results by handle",
    href="https://www.snowflake.com",
    key_help="Snowflake → Snowsight → your user menu → Settings → Authentication → Programmatic access "
             "tokens → Generate new token, restricted to a READ-ONLY role (USAGE on a warehouse, SELECT on "
             "what the agent may read): the role is the real boundary, Snowflake has no read-only token. "
             "The secret is shown once. The user must be under a network policy that allows oto, or the "
             "token is refused.",
    settings_help={
        "account": "Your account identifier in the org-account form, as in "
                   "https://<myorg-myaccount>.snowflakecomputing.com (Snowsight → account menu → "
                   "View account details → Account identifier, with a dash instead of the dot).",
    },
)

CATEGORY = "Product & data"
PUBLISHER = "Snowflake"
LOGO_DOMAIN = "snowflake.com"

DESCRIPTION = (
    "Your Snowflake data warehouse, read only: run SELECT, SHOW and DESCRIBE queries on the "
    "warehouse, role, database and schema of your choice, and read long results by their handle. "
    "A programmatic access token restricted to a read-only role, and your account identifier. "
    "Queries bill your warehouse credits."
)
