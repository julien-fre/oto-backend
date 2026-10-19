"""Registry declaration of the `waterfall` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# waterfall: a COMPOSITE with no credential of its own — each step is an ordinary call
# of another connector's tool (`tools/waterfall.py`), which resolves ITS key, passes ITS
# activation gate and bills on ITS row. Hence `secret_kind="none"`: there is nothing to
# set on this card, and activating it opens no provider — a step whose connector is off
# for the org is skipped, never reached through this one.
CONNECTOR = _c(
    "waterfall", ["waterfall"], secret_kind="none",
    label="Waterfall enrichment",
    help="email, phone and company enrichment across your connected providers, one after "
         "the other, stopping at the first hit",
)

# Publisher: ours — the cascade runs on our server; the calls it makes reach the
# providers under their own connectors, each of which names its own publisher.
PUBLISHER = "Otomata"
SANS_LOGO_DE_MARQUE = True
CATEGORY = "Prospecting"

DESCRIPTION = (
    "Native waterfall enrichment: finds a person's email or phone number, or a "
    "company's firmographics, by trying the providers you have connected one after "
    "the other and stopping at the first that finds it. Each provider is billed on "
    "its own key, as a direct call would be."
)
