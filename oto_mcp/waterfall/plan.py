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

#: What each step brings, in one line, for a screen and a person choosing their order:
#: what the source sees, and when it is worth its place. Every step of every waterfall
#: has one (`tests/waterfall/test_waterfall_plan.py`). Facts the receipts taught, never a
#: price — that is the instance's own.
EXPLAINS: dict[tuple[str, str], str] = {
    ("waterfall_email", "hunter"): "Finds the address from the name and the company's domain, and says how sure it is — strongest where the company has a public email pattern.",
    ("waterfall_email", "aiark"): "Reads the email attached to a LinkedIn profile — on a real list, mostly the same emails as Apollo.",
    ("waterfall_email", "apollo"): "Apollo's contact database — wide coverage; its match is billed even when it has no email.",
    ("waterfall_email", "kaspr"): "Kaspr's database, from a LinkedIn profile.",
    ("waterfall_email", "lusha"): "Lusha's database — from a LinkedIn profile, or name and company.",
    ("waterfall_email", "forager"): "Forager's database, from a LinkedIn profile — your own key only.",
    ("waterfall_email", "dropcontact"): "Dropcontact finds and qualifies the address — answers late, so often collected after the call.",
    ("waterfall_email", "lemlist"): "lemlist's own enrichment behind one call — your key only; answers within ~25 s.",
    ("waterfall_email", "fullenrich"): "FullEnrich runs its own waterfall of providers — slow (30 s to 4 min), usually collected later.",
    ("waterfall_phone", "kaspr"): "Kaspr's phone numbers, from a LinkedIn profile.",
    ("waterfall_phone", "lusha"): "Lusha's phone numbers — from a LinkedIn profile, or name and company.",
    ("waterfall_phone", "forager"): "Forager's phone numbers, from a LinkedIn profile.",
    ("waterfall_phone", "apollo"): "Apollo's phone reveal — your own Apollo key only; asynchronous.",
    ("waterfall_phone", "lemlist"): "lemlist's enrichment — your key only; answers after a short wait.",
    ("waterfall_phone", "dropcontact"): "Dropcontact's phone enrichment — asynchronous.",
    ("waterfall_phone", "fullenrich"): "FullEnrich runs its own waterfall of providers — usually collected later.",
    ("waterfall_company", "fr"): "The French company registry — legal name, SIREN, address, NAF code, size band; free, France only.",
    ("waterfall_company", "aiark"): "LinkedIn-sourced company profile — industry, headcount, revenue range, phone.",
    ("waterfall_company", "apollo"): "Apollo's company record — headcount, revenue, industry, LinkedIn page; needs the domain.",
    ("waterfall_company", "theirstack"): "Company record built from its job ads — size, industry, and the technologies the ads name.",
    ("waterfall_company", "cognism"): "Cognism's company data — billed per your Cognism contract.",
    ("waterfall_company", "linkedin"): "Finds the LinkedIn page when no source gave it — from the company's own website, else a search checked against the domain.",
    ("waterfall_person", "linkedin"): "Your own connected LinkedIn reads the profile live — free and freshest, rate-limited.",
    ("waterfall_person", "aiark"): "LinkedIn-sourced profile — current title, company, seniority, location.",
    ("waterfall_person", "apollo"): "Apollo's person record — title, company, seniority; can start from an email.",
    ("waterfall_person", "forager"): "Forager's profile, from a LinkedIn profile.",
    ("waterfall_person", "cognism"): "Cognism's contact record — billed per your Cognism contract.",
    ("waterfall_person", "lusha"): "Lusha's profile search, without revealing contact data.",
    ("waterfall_person", "lemlist"): "lemlist's LinkedIn enrichment — your key only; asynchronous.",
    ("waterfall_people", "apollo"): "Apollo's free people search — titles for everyone, names masked; only the ones you keep are revealed (paid).",
    ("waterfall_people", "google"): "LinkedIn profiles found on Google — full names and titles, kept only when they name the company.",
    ("waterfall_people", "aiark"): "LinkedIn-sourced people at the domain — billed per person returned.",
    ("waterfall_people", "linkedin_employees"): "The company page's employees, straight from LinkedIn — the freshest titles; needs the page URL.",
    ("waterfall_tech", "dataforseo"): "What the website runs — CMS, framework, hosting, analytics, e-commerce — from DataForSEO's index of the web.",
    ("waterfall_tech", "apify"): "The same, by crawling the site live with Apify — for a site DataForSEO has not indexed, or a stack that changed since.",
    ("waterfall_tech", "apollo"): "Apollo's company record — around a hundred tools with their categories, website and business software alike (CRM, data stack, payments).",
    ("waterfall_tech", "aiark"): "AI Ark's company record — website detections and business tools mixed, with categories; a second vendor view.",
    ("waterfall_tech", "theirstack"): "What the job ads name — CRM, ERP, data stack, internal tools the website never shows; thin on small companies.",
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
