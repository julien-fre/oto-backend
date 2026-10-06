"""The PER-ORG BUDGET of a tenant key — the tenant→org edge of 0053 (L-keys PR 2).

R10, settled on 12/08: SHARED budget — the letter of D7. The counter is the one on the
edge `tenant:{slug}:{connector} —grant→ org:{id}` (`grant_counters`), summed over the
day window for the (instance, beneficiary) pair: all members of the org draw from the
same budget, and that is intended.

**Three states, inherited from batch L5** (`grants_chain.tenant_rung`):
- MUTE (no edge ever targeted this org) → nothing to cap, nothing to debit:
  the key serves as in PR 1 — that is the promised inertia;
- GRANTED → the edge's `quota` constraint caps the day (0 or absent = unlimited,
  convention of the old path) and the edge is debited;
- REFUSED (all revoked) → the walker has already SKIPPED the rung, we never get here.

⚠️ **Debited at RESOLUTION, not on call success** — unlike the platform counter,
which each tool debits itself after a successful call
(`access.record_platform_usage`, ~10 sites). A tenant key has no such sites, and
adding one per tool would be precisely the copy the single walker exists to
avoid. The price: a call that fails at the provider still counts. The alternative —
a bound that is set but that nobody debits — is the defect of #409 (an accepted row
that nothing reads), and it is worse. Moving to "on success" will go through the
middleware's call record, with L8.
"""
from __future__ import annotations

from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import grants_chain
from ..db import grants as db_grants


def enforce(slug: str, provider: str, org: "int | None") -> None:
    """Apply the tenant→org edge budget for THIS call: raise if the day is
    exhausted, debit otherwise. No-op (no read) without an edge."""
    verdict = grants_chain.tenant_rung(slug, provider, org)
    if verdict is None or not verdict.granted or verdict.grant_id is None:
        return
    if verdict.quota:
        used = db_grants.counter_sum_today(verdict.resource_id, "org", str(org))
        if used >= verdict.quota:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"Budget for the `{provider}` key of tenant `{slug}` is exhausted today "
                    f"for this org ({used}/{verdict.quota}). The budget is shared by "
                    f"the whole org; its tenant admin can raise it."
                )))
    db_grants.bump_counter(verdict.grant_id, 1)
