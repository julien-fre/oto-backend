"""The person cascade (`waterfall_person`): a profile is a find when it names the
current title or company."""
from __future__ import annotations

from typing import Optional
from urllib.parse import unquote

from .etape import Step
from .fournisseurs import (_LEMLIST_COLLECT, _NAMES_AND_DOMAIN, _PERSON_IDS, _apollo_args,
                           _cognism_contact_args, _lemlist_args, _lemlist_first,
                           _lusha_contact, _lusha_first)
from .gens import _LEMLIST_EXCLUDES
from .valeurs import _clean, _domain, _find_key, _get, _norm_name, _pick, _slug

def _person(**fields) -> Optional[dict]:
    """A profile is a find when it names the current title or company."""
    return _clean(fields, ("title", "company_name"))


def _aiark_person(res, inputs):
    if not _get(res, "found"):
        return None
    return _person(
        full_name=_pick(res, "profile.full_name", "full_name"),
        title=_pick(res, "profile.title", "title"),
        headline=_pick(res, "profile.headline", "headline"),
        company_name=_pick(res, "company.summary.name", "company.name"),
        company_linkedin_url=_pick(res, "company.link.linkedin"),
        seniority=_pick(res, "department.seniority"),
        city=_pick(res, "location.city"),
        country=_pick(res, "location.country"),
        linkedin_url=_pick(res, "link.linkedin") or inputs.get("linkedin_url"),
    )


def _apollo_person(res, inputs):
    p = _get(res, "person") or {}
    if p.get("_stub"):
        return None
    org = p.get("organization") or {}
    return _person(
        full_name=p.get("name"), title=p.get("title"), headline=p.get("headline"),
        company_name=org.get("name"),
        company_domain=_domain(org.get("primary_domain")),
        seniority=p.get("seniority"), city=p.get("city"), country=p.get("country"),
        # The URL the caller gave, when there is one: Apollo answers it percent-encoded
        # (`…/in/sinan-oru%c3%a7…`), which matches nothing the caller holds.
        linkedin_url=inputs.get("linkedin_url") or p.get("linkedin_url"),
    )


def _generic_person(rec, inputs):
    """For providers whose person shape no test here pins: well-known key names only."""
    if not isinstance(rec, dict):
        return None
    company = _find_key(rec, ("companyName", "company_name", "organizationName",
                              "currentCompany", "employer"), 3)
    if company is None:
        c = rec.get("company")
        company = c if isinstance(c, str) else _find_key(c or {}, ("name",), 1)
    return _person(
        full_name=_find_key(rec, ("fullName", "full_name", "name"), 1),
        title=_find_key(rec, ("jobTitle", "job_title", "title", "position",
                              "occupation"), 3),
        headline=_find_key(rec, ("headline", "tagline"), 2),
        company_name=company,
        city=_find_key(rec, ("city",), 3),
        country=_find_key(rec, ("country", "countryCode"), 3),
        location=_find_key(rec, ("location", "locationName"), 2),
        linkedin_url=inputs.get("linkedin_url"),
    )


def _lemlist_person(res, inputs):
    r = _lemlist_first(res)
    raw = _get(r, "data.linkedin")
    digest = _get(r, "found.linkedin")
    return _generic_person(raw if isinstance(raw, dict) else digest, inputs)


def _plain_slug(linkedin_url: str) -> str:
    """`…/in/jane-do%C3%A7/` → `jane-doç`: lemlist keys a profile by its slug, decoded."""
    return unquote(_slug(linkedin_url)).lower()


def _lemlist_db_args(i: dict) -> dict:
    """lemlist's people database: by the LinkedIn slug (whatever the URL's form, Apollo's
    percent-encoded one included), else the full name at the company's website."""
    if i.get("linkedin_url"):
        filters = [{"filterId": "leadLinkedInSlug", "in": [_plain_slug(i["linkedin_url"])],
                    "out": []}]
    else:
        filters = [{"filterId": "username", "in": [i["full_name"]], "out": []},
                   {"filterId": "currentCompanyWebsiteUrl", "in": [i["domain"]], "out": []}]
    # By name, a few: a namesake at the same company is stepped over below.
    return {"op": "people", "filters": filters, "excludes": _LEMLIST_EXCLUDES, "page": 1,
            "size": 1 if i.get("linkedin_url") else 3}


def _lemlist_db_person(res, inputs):
    """The record of the person asked (same slug, else same name). Title: the CURRENT
    position (no end date) at the current employer (`current_exp_company_name`) — else the
    headline, when no position there is current (a profile not updated). Its
    email is a pattern guess: returned as `guessed`, never as a find of its own."""
    slug = _plain_slug(inputs["linkedin_url"]) if inputs.get("linkedin_url") else None
    for rec in (_get(res, "results") or [])[:3]:
        if not isinstance(rec, dict):
            continue
        if slug:
            if str(rec.get("linkedin_short") or "").lower() != slug:
                continue
        elif _norm_name(rec.get("full_name") or "") != _norm_name(inputs.get("full_name") or ""):
            continue
        employer = rec.get("current_exp_company_name")
        current = [e for e in rec.get("experiences") or []
                   if isinstance(e, dict) and not e.get("date_to") and employer
                   and _norm_name(e.get("company_name") or "") == _norm_name(employer)]
        exp = min(current, key=lambda e: e.get("order_in_profile") or 99) if current else {}
        email = rec.get("potential_email")
        return _person(
            full_name=rec.get("full_name"),
            title=exp.get("title") or rec.get("headline"),
            headline=rec.get("headline"),
            company_name=employer,
            company_domain=_domain(exp.get("company_domain")),
            company_linkedin_url=exp.get("company_linkedin_url"),
            seniority=rec.get("seniority"), department=rec.get("department"),
            location=rec.get("location"), country=rec.get("country"),
            linkedin_url=inputs.get("linkedin_url") or rec.get("lead_linkedin_url"),
            email=email, email_status="guessed" if email else None,
        )
    return None


PERSON_STEPS: tuple[Step, ...] = (
    Step("linkedin", "linkedin_unipile_profile", (("linkedin_url",),),
         lambda i: {"op": "person", "identifier": _slug(i["linkedin_url"])},
         _generic_person, "free — your connected LinkedIn account (rate-limited)",
         free=True, label="Your LinkedIn",
         explains=(
             "Your own connected LinkedIn reads the profile live — free and freshest, "
             "rate-limited.")),
    # Ahead of every paid profile: it bills no credits (one call of the key's quota).
    Step("lemlist_db", "lemlist_database", (("linkedin_url",), _NAMES_AND_DOMAIN),
         _lemlist_db_args, _lemlist_db_person,
         "free search (no lemlist credits; one call of the key's search quota)",
         label="lemlist database", quota_only=True,
         explains=(
             "lemlist's shared people database — current title, company, seniority, "
             "location, with a pattern-guessed email; spends no credits.")),
    Step("aiark", "linkedin_aiark_person", (("linkedin_url",),),
         lambda i: {"op": "export", "url": i["linkedin_url"]},
         _aiark_person, "AI Ark export credits",
         explains="LinkedIn-sourced profile — current title, company, seniority, location."),
    Step("apollo", "apollo_match_person", _PERSON_IDS + (("email",),),
         _apollo_args, _apollo_person, "1 Apollo credit",
         explains=(
             "Apollo's person record — title, company, seniority; can start from an email.")),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "detail",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _generic_person, "1 Forager credit (own key only)", own_key_only=True,
         asks="detail",
         explains="Forager's profile, from a LinkedIn profile."),
    Step("cognism", "cognism_enrich_contact", _PERSON_IDS + (("email",),),
         _cognism_contact_args,
         lambda res, i: _generic_person(_get(res, "results[0]"), i),
         "Cognism credits (per its contract)",
         explains="Cognism's contact record — billed per your Cognism contract."),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS + (("email",),),
         # No `reveal`: the search alone carries the profile, and is the cheap mode.
         lambda i: {"contacts": [_lusha_contact(i)]},
         lambda res, i: _generic_person(_lusha_first(res), i), "Lusha search credits",
         explains="Lusha's profile search, without revealing contact data."),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, linkedin_enrichment=True),
         _lemlist_person, "lemlist credits (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT,
         explains="lemlist's LinkedIn enrichment — your key only; asynchronous."),
)
