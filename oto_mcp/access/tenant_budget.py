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

⚠️ **Checked at RESOLUTION, debited on SUCCESS** (L8): the resolution refuses an
exhausted day and notes the edge on the call record (`tenant_grant_ids`); the edge of
the call debits it once the tool has returned without error
(`reglement.regler_appel`), at the same point as the platform counter. A call that
fails at the provider no longer counts. OUTSIDE a call record (a REST face, a
maintenance command) there is no edge to settle: the debit is made at resolution,
as before L8 — otherwise those calls would never be counted.
"""
from __future__ import annotations

from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import grants_chain, session_org
from ..db import grants as db_grants


def enforce(slug: str, provider: str, org: "int | None") -> None:
    """Apply the tenant→org edge budget for THIS call: raise if the day is
    exhausted, otherwise note the edge for the settlement of the call (debited on
    success, `regler_aretes_tenant`). No-op (no read) without an edge."""
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
    releve = session_org.current_call_trace()
    if releve is None:
        db_grants.bump_counter(verdict.grant_id, 1)
        return
    # A list, not a key: a composite tool that resolves the tenant key twice
    # debited twice before L8, and still does.
    releve.setdefault("tenant_grant_ids", []).append(verdict.grant_id)


def regler_aretes_tenant(grant_ids: list) -> None:
    """Debits the tenant edges noted by `enforce` for a call that SUCCEEDED — one
    unit per resolution, as at resolution before L8."""
    for grant_id in grant_ids:
        db_grants.bump_counter(grant_id, 1)
