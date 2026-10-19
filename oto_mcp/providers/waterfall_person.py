"""Registry declaration of the `waterfall_person` connector.

Sole home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# A COMPOSITE (`tools/waterfall.py`, shared by the four waterfall connectors): each step
# is an ordinary call of another connector's tool, which resolves ITS key, passes ITS
# activation gate and bills on ITS row. The only thing this card stores is a SETTING —
# the ordered list of steps — never a secret: `secret=False`, rendered as-is, set by an
# org admin (`byo_org`, the org default) or by a member for themselves (`byo_user`, an
# override), resolved by the same cascade as a key. Nothing set = the default order.
CONNECTOR = _c(
    "waterfall_person", ["waterfall_person"], secret_kind="fields", auth_modes={"byo_user", "byo_org"},
    cardinality="mono", modules=("waterfall",),
    credential_fields=(CredentialField(
        "steps", "Steps, in order", secret=False, required=False,
        help="optional — comma-separated provider keys, e.g. `aiark,apollo,forager,cognism,lusha,lemlist`. "
             "Empty = the default order, where providers you hold your own key for run first."),),
    label="Person waterfall",
    help="a person's current role and company, across your connected providers one after the other, stopping at the "
         "first hit",
)

# Publisher: ours — the cascade runs on our server; the calls it makes reach the
# providers under their own connectors, each of which names its own publisher.
PUBLISHER = "Otomata"
SANS_LOGO_DE_MARQUE = True
CATEGORY = "Prospecting"

DESCRIPTION = (
    "Native waterfall enrichment: finds a person's profile (current title, company, location, LinkedIn) by trying the "
    "providers you have connected one after the other and stopping at the first that "
    "finds it. You choose the providers and their order; each one is billed on its own "
    "key, as a direct call would be."
)
