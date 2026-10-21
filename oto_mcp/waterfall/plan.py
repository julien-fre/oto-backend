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

#: What a step brings that its row does not already say — what the source sees, a measured
#: behaviour, a trap — for a person choosing or reordering the steps. ONLY where there is
#: such a fact: never the inputs (the plan's `needs`), the key it needs (`key_mode`,
#: `own_key_only`) or its slowness (`asynchronous`), which a screen shows next to it; never
#: a line relative to another row (each org reorders). Facts the receipts taught, never a
#: price — that is the instance's own. `tests/waterfall/test_waterfall_plan.py`.
EXPLAINS: dict[tuple[str, str], str] = {
    ("waterfall_email", "hunter"): "Guesses the address from the company's email pattern, with a confidence score.",
    ("waterfall_email", "aiark"): "Email from LinkedIn-sourced records — on a real list, mostly the same as Apollo's.",
    ("waterfall_email", "apollo"): "A wide contact database — but a match is billed even when it holds no email.",
    ("waterfall_email", "dropcontact"): "The address comes back qualified (valid, catch-all…); one it calls invalid is dropped.",
    ("waterfall_email", "lemlist"): "lemlist's own enrichment behind one call — about 25 s when measured.",
    ("waterfall_email", "fullenrich"): "Runs FullEnrich's own waterfall of providers — 30 s to 4 min, so often collected later.",
    ("waterfall_phone", "apollo"): "Apollo reveals the number on request — a mobile is kept first when several come back.",
    ("waterfall_phone", "fullenrich"): "Runs FullEnrich's own waterfall of providers — 30 s to 4 min, so often collected later.",
    ("waterfall_company", "fr"): "The official French registry: legal name, SIREN, address, NAF code, size band — free.",
    ("waterfall_company", "aiark"): "LinkedIn-sourced profile: industry, headcount, revenue range, phone.",
    ("waterfall_company", "apollo"): "Headcount, revenue, industry and the company's LinkedIn page.",
    ("waterfall_company", "theirstack"): "Built from the company's job ads: size, industry, and the technologies they name.",
    ("waterfall_company", "linkedin"): "Fills in the LinkedIn page when no source gave it — from the website, else a search checked against the domain.",
    ("waterfall_person", "linkedin"): "Read live through your own LinkedIn — free and the freshest, but rate-limited.",
    ("waterfall_person", "aiark"): "LinkedIn-sourced profile: current title, company, seniority, location.",
    ("waterfall_person", "lusha"): "The profile only, without revealing contact data — Lusha's cheaper lookup.",
    ("waterfall_people", "apollo"): "Free search: everyone's title, names masked — you pay only to reveal the people you keep.",
    ("waterfall_people", "google"): "LinkedIn profiles found on Google — kept only when the result names the company.",
    ("waterfall_people", "aiark"): "LinkedIn-sourced people at the domain — billed per person returned.",
    ("waterfall_people", "linkedin_employees"): "The company page's employees, straight from LinkedIn — the freshest titles.",
    ("waterfall_tech", "apollo"): "Around a hundred tools with categories — website and business software alike (CRM, data, payments).",
    ("waterfall_tech", "dataforseo"): "What the website runs — CMS, framework, hosting, analytics — from DataForSEO's index.",
    ("waterfall_tech", "apify"): "What the website runs, crawled live — for a site the index lacks or a stack that changed.",
    ("waterfall_tech", "aiark"): "A second vendor record: website and business tools mixed, with categories.",
    ("waterfall_tech", "theirstack"): "What the job ads name — CRM, ERP, data and internal tools a website never shows; thin on small firms.",
}


#: Each waterfall connector's step table — what the plan below and the tools read.
STEPS_BY_NAMESPACE: dict[str, tuple[Step, ...]] = {
    "waterfall_email": EMAIL_STEPS, "waterfall_phone": PHONE_STEPS,
    "waterfall_company": COMPANY_STEPS, "waterfall_person": PERSON_STEPS,
    "waterfall_people": PEOPLE_STEPS, "waterfall_tech": TECH_STEPS,
}


def _step_row(step: Step, mode: Optional[str] = None, ns: Optional[str] = None) -> dict:
    from .. import providers
    from ..tool_visibility import namespace_of
    con = providers.connector_for_namespace(namespace_of(step.tool))
    row = {"key": step.key, "tool": step.tool,
           # The CONNECTOR the step goes through — what a screen shows (its logo, its
           # card), not the vault carrier (`fr_get` → `sirene`, aiark → `aiark`).
           "connector": con.name if con is not None else None,
           "label": step.label or (con.label if con is not None else step.key),
           # What the step brings, in one line (`EXPLAINS`).
           "explains": EXPLAINS.get((ns, step.key)) if ns else None,
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
    return row


def plan(ns: str, sub: Optional[str]) -> dict:
    """The waterfall's order for `sub` — what a call would try, spending nothing: where
    the order comes from (`setting` / `default`), each step with the key it would use,
    and every step the waterfall offers (`available`, the table's order), so a screen
    can show the cascade and let someone remove, reorder or add back a step. SYNC (it
    reads the vault and the cascade): off the loop."""
    steps = STEPS_BY_NAMESPACE[ns]
    order, ignored, source, modes = ordre._plan(ns, steps, None, sub)
    out = {"connector": ns, "order_from": source, "ignored_steps": ignored,
           "steps": [_step_row(s, modes.get(s.tool, "unknown"), ns) for s in order],
           "available": [_step_row(s, ns=ns) for s in steps]}
    if ns in qualification._QUALIFIABLE:
        out["qualification"] = qualification._qualification_row(sub, ns)
    return out
