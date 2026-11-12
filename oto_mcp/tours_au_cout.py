"""Model calls billed on their REAL cost: journaled, never priced here.

A hosted agent on a SHARED key (the instance's, or the primary tenant's) of a family
listed in `FAMILLES_AU_COUT` is not reserved nor settled in the units budget
(`access/credits_tours`): the provider answers each call with what it CHARGED for it
(OpenRouter: `usage.cost`, in USD), and oto writes that figure down, one journal line per
turn, for the commerce to bill. oto applies no rate to it: what a dollar costs the
customer is the commerce's grid, not the core's.

Why not the per-token scale of `anthropic`: one OpenRouter model id is served by several
hosts at different prices, and the host that serves a turn is chosen per request
(`provider.order`, then a fallback). A token line is a guess at that host; the cost the
router answered is not.

The line (`tool_calls`, `kind='settlement'`, `journal_jour.KIND_RELEVE`, like the Apify
and Higgsfield settlements): tool = the family (`openrouter`), `quantity` = the cost in
micro-dollars rounded UP (the `jev` rounding), `key_mode` = the rung of the key that paid,
`call_uid` = the turn's key (`credits_tours.cle_du_tour`), `run_id` = the job's run, and
in `args` the job, attempt, turn, the model the provider says it served, the token counts
and the cost in USD. A turn reported twice (the worker replays a report it got no answer
to) is journaled once.

What this does NOT do: hold before the call, or refuse a job below a floor. The
commerce cuts the key's grant when the payer has nothing left, as for any closed
platform key it bills from the journal; at worst the run in flight finishes. A turn the
worker never reports (it died between the call and the report) is not journaled.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from . import runner_models
from .calllog import truncated_args
from .db import usage
from .db.journal_jour import KIND_RELEVE

#: The model families whose calls on a shared key are journaled at the cost the provider
#: reported, instead of being reserved and settled in the units budget.
FAMILLES_AU_COUT = frozenset({"openrouter"})


def au_cout(model: Optional[str]) -> bool:
    """Is a call to `model` journaled at its cost (`FAMILLES_AU_COUT`)?"""
    return runner_models.famille(model) in FAMILLES_AU_COUT


def microdollars(cout_usd: float) -> int:
    """A cost in USD as integer micro-dollars, rounded UP. ⚠️ `round` before `ceil`: a
    float such as `3 × 1e-05` reads 3.0000000000000004e-05, which bare `ceil` makes 31."""
    return math.ceil(round(float(cout_usd) * 1e6, 6))


def cout_declare(brut: Any) -> Optional[float]:
    """The cost a worker reported, or None when it is absent or not a finite number ≥ 0."""
    if isinstance(brut, bool) or not isinstance(brut, (int, float)):
        return None
    if not math.isfinite(brut) or brut < 0:
        return None
    return float(brut)


def journaliser_tour(uid: str, *, model: str, sub: Optional[str], org: Optional[int],
                     run_id: Optional[str], servi: Optional[str], cout_usd: float,
                     tokens: dict, key_mode: Optional[str], meta: dict) -> dict:
    """Writes the journal line of one call to `model` (the catalogue id the job names; its
    family is the line's tool), `servi` being what the provider says it served. Returns
    `{journaled, microdollars}`, `journaled: False` when that turn is already written.
    Raises when the journal cannot be written, or `model` is not journaled at cost."""
    tool = runner_models.famille(model)
    if tool not in FAMILLES_AU_COUT:
        raise ValueError(f"{model!r} is not billed at cost ({sorted(FAMILLES_AU_COUT)})")
    if usage.releve_existe(KIND_RELEVE, tool, uid):
        return {"journaled": False, "microdollars": 0}
    montant = microdollars(cout_usd)
    usage.insert_tool_call({
        "kind": KIND_RELEVE, "tool": tool, "sub": sub, "org_id": org, "run_id": run_id,
        "ok": True, "duration_ms": 0, "call_uid": uid, "quantity": montant,
        "key_mode": key_mode,
        # À plat : `truncated_args` rend un objet imbriqué en texte.
        "args": truncated_args({**meta, **tokens, "model": servi or model,
                                "cost_usd": cout_usd}),
    })
    return {"journaled": True, "microdollars": montant}
