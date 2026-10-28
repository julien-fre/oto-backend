"""The plan, for a screen (`me.waterfall_plan`): what a call would try, in which order, on
which key — and every step a waterfall offers. Neutral facts only: which tools a step
calls, what it asks them for and its bounds; a price list is the instance's own, mapped
in its front, never here."""
from __future__ import annotations

from typing import Optional

from . import ordre, qualification
from .email import EMAIL_STEPS
from .etape import Step, _missing
from .gens import PEOPLE_STEPS
from .personne import PERSON_STEPS
from .societe import COMPANY_STEPS
from .tech import TECH_STEPS
from .telephone import PHONE_STEPS

#: Each waterfall connector's step table — what the plan below and the tools read.
STEPS_BY_NAMESPACE: dict[str, tuple[Step, ...]] = {
    "waterfall_email": EMAIL_STEPS, "waterfall_phone": PHONE_STEPS,
    "waterfall_company": COMPANY_STEPS, "waterfall_person": PERSON_STEPS,
    "waterfall_people": PEOPLE_STEPS, "waterfall_tech": TECH_STEPS,
}


def _step_row(step: Step, mode: Optional[str] = None) -> dict:
    from .. import providers
    from ..tool_visibility import namespace_of
    con = providers.connector_for_namespace(namespace_of(step.tool))
    row = {"key": step.key, "tool": step.tool,
           # The CONNECTOR the step goes through — what a screen shows (its logo, its
           # card), not the vault carrier (`fr_get` → `sirene`, aiark → `aiark`).
           "connector": con.name if con is not None else None,
           "label": step.label or (con.label if con is not None else step.key),
           # What the step brings, in one line (`Step.explains`).
           "explains": step.explains,
           "needs": _missing(step),
           # The same thing, as data: alternative groups of input keys (`fr` = a French
           # company) — what a screen translates, where `needs` is a sentence for agents.
           "requires": [list(g) for g in step.requires],
           "cost": step.cost, "free": step.free,
           # A completion step: runs after the cascade, only to fill this missing field.
           "fills": step.fills or None,
           "asynchronous": step.collect is not None,
           # The other tools the step calls: the one that collects an asynchronous
           # answer, the one that reveals ONE of a free search's finds (per person).
           "collect_tool": step.collect.tool if step.collect is not None else None,
           "reveal": step.reveal or None,
           # What it asks a tool that bills by what is asked, and its bounds.
           "asks": step.asks or None,
           "max_units": step.max_units,
           "per_item": step.per_item}
    if mode is not None:
        row["key_mode"] = mode
        row["usable"] = mode not in ordre._NO_KEY and not ordre.own_key_missing(step, mode)
    if step.own_key_only:
        row["own_key_only"] = True
    if step.quota_only:
        # No credits billed — a screen prices it at nothing, but it spends the key's quota.
        row["quota_only"] = True
    return row


def plan(ns: str, sub: Optional[str]) -> dict:
    """The waterfall's order for `sub` — what a call would try, spending nothing: where
    the order comes from (`setting` / `default`), each step with the key it would use,
    and every step the waterfall offers (`available`, the table's order), so a screen
    can show the cascade and let someone remove, reorder or add back a step. SYNC (it
    reads the vault and the cascade): off the loop."""
    # Each step as THIS call sees it (`ordre._opened`: the instance's settings read once).
    steps = tuple(ordre._opened(s) for s in STEPS_BY_NAMESPACE[ns])
    order, ignored, source, modes = ordre._plan(ns, steps, None, sub)
    out = {"connector": ns, "order_from": source, "ignored_steps": ignored,
           "steps": [_step_row(s, modes.get(s.tool, "unknown")) for s in order],
           "available": [_step_row(s) for s in steps]}
    if ns in qualification._QUALIFIABLE:
        out["qualification"] = qualification._qualification_row(sub, ns)
    return out
