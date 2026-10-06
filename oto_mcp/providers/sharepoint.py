"""Registry declaration of the `sharepoint` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# sharepoint: Microsoft 365 files (SharePoint sites, document libraries,
# OneDrive) via Microsoft Graph, ON BEHALF OF THE PERSON: each one
# connects with their Microsoft 365 account (OAuth, delegated permissions) and the agent
# sees exactly what they see — several accounts possible, chosen by
# `_account=` at call time. The application is oto's own, multi-tenant,
# whose credentials are set at the platform tier (`auth/microsoft.py`); the
# client registers no application. The Graph host is fixed: no egress
# guard to set.
CONNECTOR = _c(
    "sharepoint", ["sharepoint"],
    auth_modes={"byo_user"},
    # Consent comes from the person's Microsoft account, not from their org.
    personal_session=True, secret_kind="oauth",
    # OAuth ⟹ the derivation would say single; yet a person links several Microsoft
    # accounts (their directory, a client's) and the vault holds one row per
    # account (`auth/microsoft.persist_grant`). Same provider reason as google.
    cardinality="multi",
    label="SharePoint & OneDrive",
    help="your Microsoft 365 files: sites, libraries, OneDrive — search, read, "
         "upload, with your rights",
    href="https://learn.microsoft.com/graph/api/resources/sharepoint",
)

CATEGORY = "Knowledge"
PUBLISHER = "Microsoft"
LOGO_DOMAIN = "microsoft.com"

DESCRIPTION = (
    "Your Microsoft 365 files: your OneDrive, the SharePoint sites and the "
    "document libraries you have access to. Search, read a document "
    "(Word, PDF, Excel…) and upload one. You connect with your Microsoft "
    "account: the agent sees what you see, no more, no less."
)
