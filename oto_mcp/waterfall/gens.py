"""The people-at-a-company cascade (`waterfall_people`): its steps, in their default
order, and how each source's people are read — a name, a title that matches the roles
asked, decision-makers first, and only people whose CURRENT company is the one asked."""
from __future__ import annotations

import re
from typing import Optional

from .etape import Step
from .fournisseurs import _APIFY_EMPLOYEES_TIMEOUT_S, _apify_items
from .valeurs import _get, _norm_name, _pick

def _matches_roles(title, roles) -> bool:
    if not roles:
        return True
    t = _norm_name(str(title or ""))
    return any(_norm_name(r) and f" {_norm_name(r)} " in f" {t} " for r in roles)


_SENIORITY_WORDS = (
    ("founder", "co founder", "owner", "ceo", "chief", "president", "managing director",
     "geschaftsfuhrer", "general manager", "c suite", "cto", "cfo", "coo", "cmo", "cro",
     # French and German titles (a French retailer's head office reads in French).
     "fondateur", "fondatrice", "pdg", "directeur general", "directrice generale",
     "gerant", "gerante", "inhaber", "vorstand",
     # lemlist's seniority levels.
     "executive leadership", "firm leadership"),
    ("vp", "vice president", "head", "director", "partner", "directeur", "directrice",
     "leiter", "leiterin", "bereichsleiter", "department leadership"),
    ("manager", "lead", "leader", "senior", "responsable", "chef", "manageur",
     "people management"),
)


def _seniority_rank(title, seniority) -> int:
    """0 = the top (founder, C-level), then VP/head/director, then manager/lead, then
    the rest — read from the provider's seniority when it has one, else the title."""
    # No nested same-quote f-string: the floor is Python 3.10.
    raw = " ".join(str(x) for x in (seniority, title) if x).replace("_", " ")
    text = f" {_norm_name(raw)} "
    for rank, words in enumerate(_SENIORITY_WORDS):
        if any(f" {w} " in text for w in words):
            return rank
    return len(_SENIORITY_WORDS)


def _people_find(people: list[dict], inputs: dict) -> Optional[dict]:
    """The people that name a person and match the roles asked, deduplicated by
    LinkedIn URL (else name), at most `limit` — a find when at least one remains."""
    out, seen = [], set()
    # Decision-makers first: a source returns people in its own order (an engineer ahead
    # of the CTO, measured on a real company), and `limit` would cut the wrong end.
    people = sorted(people, key=lambda p: _seniority_rank(p.get("title"), p.get("seniority")))
    for p in people:
        p = {k: v for k, v in p.items() if v not in (None, "", [], {})}
        if not p.get("full_name") or not _matches_roles(p.get("title"), inputs.get("roles")):
            continue
        key = (p.get("linkedin_url") or p["full_name"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= (inputs.get("_pool") or inputs["limit"]):
            break
    return {"people": out, "count": len(out)} if out else None


def _apollo_people(res, inputs):
    rows = []
    for p in (_get(res, "people") or []) + (_get(res, "contacts") or []):
        if not isinstance(p, dict):
            continue
        last = p.get("last_name") or p.get("last_name_obfuscated") or ""
        rows.append({
            "full_name": p.get("name") or " ".join(x for x in (p.get("first_name"), last) if x),
            "title": p.get("title"), "linkedin_url": p.get("linkedin_url"),
            "location": ", ".join(x for x in (p.get("city"), p.get("country")) if x) or None,
            # Apollo's search obfuscates last names: reveal with `apollo_match_person`
            # by this id, NEVER by name + company (it would mint an empty, billed record).
            "apollo_id": p.get("id"),
            "name_obfuscated": True if p.get("last_name_obfuscated") else None,
            # What a reveal would bring — what tells a caller whom to spend it on.
            "has_email": p.get("has_email"),
            "has_direct_phone": (str(p.get("has_direct_phone")).lower().startswith("yes")
                                 if p.get("has_direct_phone") is not None else None),
        })
    return _people_find(rows, inputs)


def _aiark_people(res, inputs):
    rows = []
    for rec in _get(res, "content") or []:
        if isinstance(rec, dict):
            rows.append({
                "full_name": _pick(rec, "profile.full_name"),
                "title": _pick(rec, "profile.title", "profile.headline"),
                "linkedin_url": _pick(rec, "link.linkedin"),
                "location": _pick(rec, "location.default", "location.city"),
                "seniority": _pick(rec, "department.seniority"),
            })
    return _people_find(rows, inputs)


def _company_slug(url: Optional[str]) -> Optional[str]:
    m = re.search(r"linkedin\.com/(?:company|school|showcase)/([^/?#\s]+)", url or "", re.I)
    return m.group(1).lower() if m else None


#: What lemlist drops from each record when asked (`excludes`): parts no step reads. It
#: does NOT drop `experiences` (~90 % of a record: the companies' descriptions and tech) —
#: nor any nested path, measured.
_LEMLIST_EXCLUDES = ["education", "skills", "languages", "interests", "summary",
                     "shorthand_name_list", "lead_logo_url"]


def _lemlist_args(i: dict) -> dict:
    """lemlist's people database, by the company's website (else its LinkedIn page) and
    the roles asked. `country` is left out: an autocomplete filter, a form it does not
    know (`FR`) silently empties the search."""
    if i.get("domain"):
        company = {"filterId": "currentCompanyWebsiteUrl", "in": [i["domain"]], "out": []}
    else:
        company = {"filterId": "currentCompanyLinkedInUrl",
                   "in": [f"https://www.linkedin.com/company/"
                          f"{_company_slug(i['company_linkedin_url'])}"], "out": []}
    filters = [company]
    if i.get("roles"):
        filters.append({"filterId": "currentTitle", "in": list(i["roles"]), "out": []})
    # A free search: under `qualify`, whole pages — the pool Jev judges on titles. Each
    # call is one of the key's search quota, whatever its size.
    return {"op": "people", "filters": filters, "excludes": _LEMLIST_EXCLUDES, "page": 1,
            "size": 100 if i.get("_pool") else min(100, i["limit"] * 2)}


def _lemlist_here(exp: dict, domain: str, slug: Optional[str]) -> bool:
    if exp.get("date_to"):
        return False
    if slug and _company_slug(exp.get("company_linkedin_url")) == slug:
        return True
    where = str(exp.get("company_domain") or "").lower()
    return bool(domain and where and (where == domain or where.endswith("." + domain)))


def _lemlist_people(res, inputs):
    """lemlist's people database. A person counts only through a CURRENT position (no
    end date) at the company asked — their title is that position's, not the headline
    (« … @ Other Corp » on someone still in post, measured). The roles are lemlist's own
    filter (`currentTitle`: words in any order, normalized titles — « Head of Enterprise
    Sales » for « Head of Sales », « Directeur des ressources humaines » for « director »),
    not matched again here: the stricter match drops those, measured. Its email is a
    pattern guess: returned as `guessed` (Apollo's word for it)."""
    domain = (inputs.get("domain") or "").lower()
    slug = _company_slug(inputs.get("company_linkedin_url"))
    rows = []
    for rec in _get(res, "results") or []:
        if not isinstance(rec, dict):
            continue
        here = [e for e in rec.get("experiences") or []
                if isinstance(e, dict) and _lemlist_here(e, domain, slug)]
        if not here:
            continue
        exp = min(here, key=lambda e: e.get("order_in_profile") or 99)
        email = rec.get("potential_email")
        rows.append({
            "full_name": rec.get("full_name"),
            "title": exp.get("title") or rec.get("headline"),
            "linkedin_url": rec.get("lead_linkedin_url"),
            "location": rec.get("location"), "seniority": rec.get("seniority"),
            "email": email, "email_status": "guessed" if email else None,
        })
    return _people_find(rows, {**inputs, "roles": None})


def _employees(res, inputs):
    """LinkedIn employees of the company page. A page's employee list pulls in people
    whose CURRENT position is elsewhere (and fake profiles): kept only when their current
    company is the page asked for."""
    slug = _company_slug(inputs.get("company_linkedin_url"))
    rows = []
    for item in _apify_items(res):
        if not isinstance(item, dict):
            continue
        current = _get(item, "currentPosition[0]") or {}
        where = str(_pick(current, "companyUniversalName", "companyLinkedinUrl",
                          "companyUrl") or "").lower()
        if slug and where and slug not in where:
            continue
        first, last = item.get("firstName"), item.get("lastName")
        rows.append({
            "full_name": _pick(item, "fullName", "name")
            or " ".join(x for x in (first, last) if x) or None,
            "title": _pick(current, "position", "title") or _pick(item, "headline"),
            "linkedin_url": _pick(item, "linkedinUrl", "profileUrl", "url"),
            "location": _pick(item, "location.linkedinText", "location", "locationName"),
        })
    return _people_find(rows, inputs)


_LINKEDIN_RESULT = re.compile(r"^\s*(?P<name>[^|\-–—]+?)\s*[-–—]\s*(?P<rest>.+?)(?:\s*\|\s*LinkedIn.*)?$")


def _serper_people(res, inputs):
    """Google results on `linkedin.com/in`. The `subtitle` is the reliable part —
    « Location · Title · Company » (or « Location · Company ») — the `title` varies
    (« Name – Company », « Name - Headline »). A result counts only when the COMPANY it
    names is the one asked: a snippet merely mentioning the words is not (« … additive
    marking techniques … » is someone else, measured)."""
    asked = _norm_name(inputs.get("company_name") or (inputs.get("domain") or "").split(".")[0])
    rows = []
    for o in _get(res, "organic") or []:
        if not isinstance(o, dict) or "linkedin.com/in/" not in str(o.get("link", "")):
            continue
        m = _LINKEDIN_RESULT.match(str(o.get("title", "")))
        if not m:
            continue
        name = m.group("name").strip()
        sub = [x.strip() for x in str(o.get("subtitle") or "").split("·") if x.strip()]
        rest = [x.strip() for x in re.split(r"\s[-–—]\s", m.group("rest")) if x.strip()]
        if sub:
            company = sub[-1]
            title = sub[1] if len(sub) >= 3 else None
        else:
            # No subtitle: « Name – Title – Company », or « Name – Company ».
            company = rest[-1] if rest else ""
            title = rest[0] if len(rest) >= 2 else None
        if asked and f" {asked} " not in f" {_norm_name(company)} ":
            continue
        rows.append({"full_name": name, "title": title,
                     "location": sub[0] if len(sub) >= 2 else None,
                     "linkedin_url": str(o["link"]).split("?")[0]})
    return _people_find(rows, inputs)


def _role_query(inputs: dict) -> str:
    roles = inputs.get("roles") or ["director", "manager", "head", "CEO", "founder"]
    return " OR ".join(f'"{r}"' if " " in r else r for r in roles)


PEOPLE_STEPS: tuple[Step, ...] = (
    Step("apollo", "apollo_search_people", (("domain",),),
         lambda i: {k: v for k, v in {
             "domains": [i["domain"]], "titles": i.get("roles"),
             "person_locations": [i["country"]] if i.get("country") else None,
             # A FREE search: under `qualify`, a whole page — the pool Jev judges on titles.
             "per_page": 100 if i.get("_pool") else min(100, i["limit"] * 2)}.items() if v},
         _apollo_people, "free search (names obfuscated; reveal per person: 1 Apollo credit)",
         reveal="apollo_match_person",
         # Masked last names only: a fallback, never the answer while a source that
         # names people fully is left to try.
         weak=lambda found: all(p.get("name_obfuscated") for p in found["people"]),
         explains=(
             "Apollo's free people search — titles for everyone, names masked; only the "
             "ones you keep are revealed (paid).")),
    Step("lemlist", "lemlist_database", (("domain",), ("company_linkedin_url",)),
         _lemlist_args, _lemlist_people,
         "free search (no lemlist credits; each page is one call of the key's search quota)",
         label="lemlist database", pages=("page", 1), quota_only=True,
         explains=(
             "lemlist's shared people database — full names, current titles and LinkedIn "
             "URLs, with a pattern-guessed email; spends no credits.")),
    # Then full names, by provider cost: google (a search, names + LinkedIn URLs, low
    # recall) → aiark (per profile) → the page's LinkedIn employees (an Apify run).
    Step("google", "serper_search", (("company_name",), ("domain",)),
         lambda i: {"query": f'"{i.get("company_name") or i["domain"].split(".")[0]}" '
                             f"({_role_query(i)})",
                    "site_filter": "linkedin.com/in", "num": 10, "country": None,
                    "language": None},
         _serper_people, "1 Serper search", label="Google — LinkedIn profiles",
         pages=("page", 1),
         explains=(
             "LinkedIn profiles found on Google — full names and titles, kept only when "
             "they name the company.")),
    Step("aiark", "linkedin_aiark_search", (("domain",),),
         lambda i: {"op": "people",
                    "account": {"domain": {"any": {"include": [i["domain"]]}}},
                    # Billed PER PROFILE returned: ask what the call keeps, not 3×.
                    "size": i["limit"]},
         _aiark_people, "AI Ark credits per person returned", pages=("page", 0),
         per_item=True,
         explains="LinkedIn-sourced people at the domain — billed per person returned."),
    Step("linkedin_employees", "apify_run_sync", (("company_linkedin_url",),),
         lambda i: {"actor_id": "harvestapi/linkedin-company-employees",
                    "run_input": {"companies": [i["company_linkedin_url"]],
                                  "maxItems": i["limit"]},
                    "max_items": i["limit"],
                    "timeout_secs": _APIFY_EMPLOYEES_TIMEOUT_S},
         _employees, "Apify, ~$0.003-0.008 per profile",
         label="LinkedIn employees", per_item=True,
         explains=(
             "The company page's employees, straight from LinkedIn — the freshest titles; "
             "needs the page URL.")),
)
