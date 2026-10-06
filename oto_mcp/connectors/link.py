"""« Is this account linked? » — declared by the module that holds the credential.

**The gap this closes.** `access.status_for` fills `me.providers[…]` with THREE loops:
keyed connectors (`db.KEY_PROVIDERS`), those with fields (`secret_fields`), and those
with a browser session (`secret_kind == "cookie"`). Connectors with an OAuth credential —
google, and until 2026-09-09 atlassian and folkmcp — are in none of them:
`keyed=False`, `secret_fields=0`, `secret_kind='oauth'`. So they had **no entry at
all**, and the cascading consequences were known to nobody:

- the `pending_action` decoration iterates existing entries → a `status_hints` hook
  on these connectors would have been **physically unreachable**;
- `health_ko` likewise;
- the card verdict (`connectorVerdict`, dashboard) reads `me.providers[name]` → it
  had nothing to read;
- **and that is WHY the front had connector names in its URLs**:
  the dashboard widget calls `/api/<name>/oauth/status` because it has no
  state to read in `/api/me`. The name-in-the-URL was not a style lapse,
  it was a workaround for this gap.

**Why a seam rather than a fourth loop that reads the vault.** They do not store
their credential in the same place: google writes one row PER ACCOUNT
(`account = email`) with its satellites in `meta`, whereas atlassian and folkmcp
wrote at the LEGACY scope `("user", sub)`. A generic loop that read the
vault itself would get at least one of them wrong, silently. Each module
knows, and says so.

⚠️ **Only one declarer remains since the removal of MCP federation**
(2026-09-09, ADR 0069): google. The seam does not fold back for that reason — it is
exactly the pattern a future OAuth connector will reuse, and its value
never depended on the number of occupants.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkState:
    """What the connector can say about its link, in the CONSUMER's vocabulary.

    Deliberately sparse: `status_for` then translates it into `ProviderStatus` (the shape
    the dashboard reads). A module should not have to know that contract.

    `health_ko`/`health_reason` (oto#25 lot a, 2026-09-04) close the second gap
    named by the module docstring above (« health_ko likewise »): the generic batch
    in `access.status_for` reads health ONLY on MEMBER-tier keys
    (`credentials_store.list_credentials(MEMBER, member_id(org, sub))`), so never
    on the LEGACY scope `("user", sub)` where these credentials live. The module KNOWS
    which scope it stores its row under — so it reads its own health and carries it here rather than
    letting a fourth generic loop guess (same reason for being as this
    file: « a generic loop would get it silently wrong »)."""
    linked: bool
    set_at: Optional[str] = None
    accounts: int = 0          # multi-account (google): how many accounts are linked
    # `None` as long as nothing has been observed — never `False` (see `ProviderStatus`,
    # `capabilities/connectors/provider_status.py`): this reader cannot confirm
    # good health, only report its REJECTION, once written.
    health_ko: Optional[bool] = None
    health_reason: Optional[str] = None


_READERS: dict[str, Callable[[str], LinkState]] = {}


def register(connector: str, read: Callable[[str], LinkState]) -> None:
    """Declares how to read this connector's link state. Called at MODULE level,
    like `status_hints.register_state`: it is a pure declaration."""
    _READERS[connector] = read


def has(connector: str) -> bool:
    return connector in _READERS


def entries() -> tuple[str, ...]:
    return tuple(_READERS)


def state(connector: str, sub: str) -> Optional[LinkState]:
    """Link state, or `None` if the connector declares none / if the read breaks.

    Fail-open: `/api/me` must NEVER go down because a third-party provider coughs.
    A `None` makes the entry absent — exactly the state before this module, so a
    degradation and not a regression."""
    read = _READERS.get(connector)
    if read is None:
        return None
    try:
        return read(sub)
    except Exception:  # noqa: BLE001
        logger.warning("connector_link: read of %s failed (fail-open)", connector,
                       exc_info=True)
        return None
