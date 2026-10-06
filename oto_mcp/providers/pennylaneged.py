"""Registry declaration of the `pennylaneged` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# pennylaneged: Pennylane GED (document tray) via the SPA's PRIVATE API
# (`app.pennylane.com/companies/{cid}/dms`, cookie + rotating CSRF). DISTINCT from the
# keyed `pennylane` connector (public API): credential = browser session,
# not an API key → the public API carries no DMS scope. Execution =
# **Browserbase**: the user logs in once via Live View (`pennylaneged_connect_start`),
# their session persists in a Context = the credential (vault). Upload =
# control plane here (presigned S3 URL) + PUT of the bytes LOCALLY (GDPR, issue #31).
# Experimental (reverse-engineered internal API): outside the core set, installable from the library.
# **byo_org**: the session can be configured at USER, TEAM or ORG level
# (accounting-firm case: a single Pennylane connection shared by the team to push
# into client GEDs — cascade user > group > org). `personal_session=True`
# remains = "browser session" category on the UI side (orthogonal to sharing).
# Two tool modules: the GED (`pennylaneged`) and the SESSION (Live View login +
# verification probe). Split on 2026-09-03 — the login probe has nothing to do
# with the document tray, and mixing them had ended up putting 600 lines in a
# file. Order matters: `pennylaneged` is imported first, `pennylaneged_session`
# derives from it (origin + error seams).
CONNECTOR = _c(
    "pennylaneged", ["pennylaneged"], auth_modes={"byo_user", "byo_org"},
    modules=("pennylaneged", "pennylaneged_session"),
    personal_session=True, secret_kind="cookie",
    label="Pennylane GED",
    help="Pennylane document tray (Browserbase session)",
    publisher="Pennylane", href="https://app.pennylane.com",
)

CATEGORY = "Finance"
LOGO_DOMAIN = "pennylane.com"

DESCRIPTION = (
    "Pennylane's document tray (GED), via your Pennylane session connected "
    "through a hosted browser — not the public API key of the "
    "`pennylane` connector, which has no access to these documents. Configurable at the level "
    "of a user, a team or the whole organization."
)
