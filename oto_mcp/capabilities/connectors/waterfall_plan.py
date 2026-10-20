"""`me.waterfall_plan` — what a waterfall connector would try for me, in which order,
and what else it offers: the screen behind « see your cascade, remove a step, put one
back » (`docs/waterfall.md`).

A READ, and nothing is spent: the plan is the cascade's own ordering
(`waterfall.plan.plan` — the same function a call uses, never a second copy of the
rule), the `steps` setting resolved by the same cascade as a key, and each step's key
mode read before anything is called. Writing the order stays the card's ordinary
credential gesture (`me.credential.set`, `org.secret.set`): there is no second way to
set it.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES


class WaterfallPlanInput(BaseModel):
    name: str                                    # the waterfall connector, from the path


class WaterfallStep(BaseModel):
    key: str = Field(description="The step's key — what the `steps` setting lists.")
    tool: str
    connector: Optional[str] = Field(default=None, description=(
        "The connector the step goes through (its card, its logo)."))
    label: str
    explains: Optional[str] = Field(default=None, description=(
        "What the step brings, in one line: what its source sees, and when it is worth "
        "its place in the order."))
    needs: str = Field(description="The inputs the step needs to run, in words.")
    requires: list[list[str]] = Field(description=(
        "The same, as data: alternative groups of input keys — the step runs when every "
        "key of at least one group is given (`fr` = a French company)."))
    cost: str = Field(description="The provider's own billing, in words.")
    free: bool
    collect_tool: Optional[str] = Field(default=None, description=(
        "For an asynchronous step, the tool that collects its answer."))
    reveal: Optional[str] = Field(default=None, description=(
        "For a free search, the tool that reveals ONE of its finds, called per person."))
    asks: Optional[str] = Field(default=None, description=(
        "What the step asks a tool that bills by what is asked (`work_emails`, "
        "`phones`, `phone_numbers`, `detail`)."))
    max_units: Optional[int] = Field(default=None, description=(
        "The most units one call can bill (a search by name steps over namesakes)."))
    per_item: bool = Field(default=False, description=(
        "Billed per result returned, up to the call's `limit`."))
    fills: Optional[str] = Field(default=None, description=(
        "A completion step: it runs after the cascade, only when the find still lacks "
        "this field (`linkedin_url`)."))
    asynchronous: bool
    key_mode: Optional[str] = Field(default=None, description=(
        "The key the step would use (`user` | `group` | `org` | `tenant` | `platform` "
        "| `open` | `forbidden` | `over_quota` | `unknown`) — on `steps` only."))
    usable: Optional[bool] = Field(default=None, description=(
        "False when no key is usable: the step is skipped without being called."))
    own_key_only: bool = Field(default=False, description=(
        "The step runs on your own key for its provider only: the shared key does not "
        "serve it."))


class WaterfallPlan(BaseModel):
    connector: str
    order_from: str = Field(description="`setting` (the card's `steps`) or `default`.")
    ignored_steps: list[str] = Field(description=(
        "Keys in the setting that are not steps of this waterfall — ignored."))
    steps: list[WaterfallStep] = Field(description="What a call would try, in order.")
    available: list[WaterfallStep] = Field(description=(
        "Every step the waterfall offers, in its default order."))
    qualification: Optional[dict] = Field(default=None, description=(
        "The optional Jev pass (`qualify=` on a call) — NOT a step: it judges what the "
        "steps found. `key_mode`, `usable` (a shared tenant/platform key), `shadowed` (a "
        "closer key that Jev refuses), `fields` (what is sent to Jev). On company, person "
        "and people only. Asked for while not `usable`, a call is refused before anything "
        "is spent."))


async def _plan(ctx: ResolvedCtx, inp: WaterfallPlanInput) -> dict:
    from ...waterfall import plan
    if inp.name not in plan.STEPS_BY_NAMESPACE:
        raise AuthzDenied(404, "not_a_waterfall",
                          f"“{inp.name}” is not a waterfall connector — one of "
                          f"{sorted(plan.STEPS_BY_NAMESPACE)}.")
    return await run_in_threadpool(plan.plan, inp.name, ctx.sub)


CAPABILITIES += [
    Capability(
        key="me.waterfall_plan",
        handler=_plan,
        Input=WaterfallPlanInput,
        authz=SUB_ONLY,
        Output=WaterfallPlan,
        mcp=None,     # a screen's read: an agent gets the same plan from `dry_run`
        errors=(DeclaredError(404, "not_a_waterfall",
                              "The connector named in the path is not a waterfall."),),
        rest=RestBinding("GET", "/api/me/waterfalls/{name}/plan"),
        description=("A waterfall connector's plan for me: the steps a call would try, "
                     "in order, with the key each would use, and every step it offers "
                     "— nothing is called."),
    ),
]
