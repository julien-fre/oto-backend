"""LinkedIn sending limits, MEMBER side: read and set the limits of MY LinkedIn accounts.

- `GET /api/me/unipile/limits` → each of my LinkedIn accounts connected in the current
  org, with every limit (`max`, `used` on its rolling window, `resets_at`) and what the
  card suggests;
- `PUT /api/me/unipile/limits` → set (a whole number) or remove (`null`) limits of ONE
  of them.

The rule lives in `oto_mcp/unipile_limites.py` (`docs/unipile.md` §Limites d'envoi).
**Only the account's owner sets them**: an account lent to a colleague is bound by its
owner's limits, the colleague cannot lift them. A limit is per ACCOUNT, not per org: the
same account linked in two orgs shows, and obeys, the same limits in both.

**No MCP face here** (`mcp=None`): an agent reads the limits of the account it acts
with through `linkedin_unipile_account(op="limits")`; setting them is the person's
gesture, on the card.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import access
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ME = "/api/me/unipile/limits"
_PROVIDER = "LINKEDIN"


class LimitsInput(BaseModel):
    """No parameters: my LinkedIn accounts in my active org."""


class LimitsSetInput(BaseModel):
    account_id: str
    # `{limit: n | null}` — a limit not named keeps its value; `null` removes it.
    limits: dict[str, Optional[int]]


class SendingLimit(BaseModel):
    """One limit of an account. `max` = None: no limit set (nothing is held back).
    `used` = actions done on the rolling window (`day` = 24 h, `week` = 7 days), counted
    whether or not a limit is set. `suggested` = what the card proposes;
    `provider_max` = above it, the provider warns the account risks a restriction."""
    label: str
    action: str
    window: str
    max: Optional[int] = None
    used: int = 0
    resets_at: Optional[str] = None
    suggested: int
    provider_max: int


class AccountLimits(BaseModel):
    account_id: str
    account_name: Optional[str] = None
    is_default: bool = False
    limits: dict[str, SendingLimit]


class LimitsView(BaseModel):
    """`accounts` = my LinkedIn accounts connected in this org, empty when none."""
    accounts: list[AccountLimits]
    max_value: int


_NOT_MINE = DeclaredError(404, "account_not_connected",
                          "the account is not one of my LinkedIn accounts in this org")
_INVALID = DeclaredError(400, "invalid_limits", "an unknown limit, or a value that is not "
                         "a whole number from 0 to the input bound (or null)")


def _mine(sub: str) -> list[dict]:
    from ..connectors import identities as connector_identities
    org_id = access.current_org(sub)
    if org_id is None:
        return []
    return connector_identities.comptes_du_canal_ici(sub, _PROVIDER, org=org_id)


def _account_view(account: dict, default_id: Optional[str]) -> dict:
    from .. import unipile_limites
    return {"account_id": account["account_id"],
            "account_name": account.get("account_name"),
            "is_default": account["account_id"] == default_id,
            "limits": unipile_limites.vue(account["account_id"])}


def _default_id(accounts: list[dict]) -> Optional[str]:
    from .. import db
    d = db.default_unipile_account(accounts)
    return d["account_id"] if d else None


def _get(ctx: ResolvedCtx, inp: LimitsInput) -> dict:
    from .. import unipile_limites
    mine = _mine(ctx.sub)
    default_id = _default_id(mine)
    return {"accounts": [_account_view(a, default_id) for a in mine],
            "max_value": unipile_limites.SAISIE_MAX}


def _set(ctx: ResolvedCtx, inp: LimitsSetInput) -> dict:
    from .. import unipile_limites
    from ..db import unipile_limites as store
    mine = _mine(ctx.sub)
    cible = next((a for a in mine if a["account_id"] == inp.account_id), None)
    if cible is None:
        raise AuthzDenied(404, "account_not_connected",
                          f"`{inp.account_id}` is not one of your LinkedIn accounts "
                          "connected in this org: only its owner sets its limits.")
    try:
        valeurs = unipile_limites.valider(inp.limits)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_limits", str(e))
    if valeurs:
        store.poser(inp.account_id, ctx.sub, valeurs)
    return _account_view(cible, _default_id(mine))


_DOC_GET = (
    "The sending limits of each of my LinkedIn accounts connected in the current org: for "
    "every limit (invitations per day and per week, messages per day, profile visits per "
    "day) its `max` (null = no limit), what was `used` on its rolling window, when a slot "
    "frees up (`resets_at`), the `suggested` value and the provider's recommended maximum "
    "(`provider_max`). Limits are per account: the same account linked in another org "
    "shows the same values."
)
_DOC_SET = (
    "Sets (a whole number, 0 pauses the action) or removes (null) sending limits of ONE "
    "of my LinkedIn accounts; a limit not named keeps its value. Every agent, recipe and "
    "colleague acting with this account is held to them: past a limit the action is "
    "refused (`linkedin_sending_limit_reached`) and nothing is sent. Only the account's "
    "owner can set them (404 `account_not_connected` otherwise)."
)

CAPABILITIES += [
    Capability(
        key="me.unipile.limits", handler=_get, Input=LimitsInput, authz=SUB_ONLY,
        Output=LimitsView, description=_DOC_GET, mcp=None,
        rest=RestBinding("GET", _ME),
    ),
    Capability(
        key="me.unipile.limits_set", handler=_set, Input=LimitsSetInput, authz=SUB_ONLY,
        Output=AccountLimits, description=_DOC_SET, errors=(_NOT_MINE, _INVALID),
        mcp=None,
        rest=RestBinding("PUT", _ME),
    ),
]
