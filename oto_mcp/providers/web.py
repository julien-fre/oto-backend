"""Registry declaration of the `web` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# The reader that ESCALATES (#348): bare fetch → serper scraper → opt-in
# disposable browser. BARE capability (ADR 0010: the providers of the tiers
# are not substitutable by the caller, it is a cascade); no credential of
# its own — each tier resolves its own (serper via the cascade,
# Browserbase via the platform config).
CONNECTOR = _c(
    "web", ["web"], secret_kind="none",
    label="Web page reader",
    help="read a public web page, even when it resists — fetch, then "
         "scraper, then disposable browser (paid, on request)",
)

CATEGORY = "Web"
# Publisher: BARE in-house capability — our cascade provides the service, and none
# of its tiers is a service the caller chooses. DECLARED, not derived from a
# default: since 2026-09-02 there is none (`Connector.publisher_name`).
PUBLISHER = "Otomata"
SANS_LOGO_DE_MARQUE = True

DESCRIPTION = (
    "Read a public web page, even when it resists a simple fetch: the "
    "reader escalates on its own — bare fetch, then scraper, then disposable "
    "browser as a last resort (paid, on explicit request). Not a search "
    "engine: give it a URL, not a query."
)
