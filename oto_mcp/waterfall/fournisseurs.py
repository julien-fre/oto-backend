"""What the providers shared by several cascades receive (Apollo, Lusha, Cognism,
lemlist, FullEnrich, Dropcontact), the collect calls of the asynchronous ones, and the
bounds of an Apify run."""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .etape import Collect
from .valeurs import _domain, _get, _slug

logger = logging.getLogger(__name__)

_NAMES_AND_DOMAIN = ("first_name", "last_name", "domain")
_NAMES_AND_COMPANY = ("first_name", "last_name", "company_name")
_PERSON_IDS = (("linkedin_url",), _NAMES_AND_DOMAIN, _NAMES_AND_COMPANY)


def _forager_shared(op: str) -> Callable[[], bool]:
    """`Step.shared_serves` of a Forager step asking `forager_person op=<op>`: whether
    THIS instance opens that op on its shared key (`platform.ops`). A setting the tool no
    longer understands, or a read that fails, serves nothing here — said in the log: the
    tool itself would refuse the call."""
    def serves() -> bool:
        from ..tools import forager
        try:
            ops = forager.platform_ops()
        except Exception:  # noqa: BLE001 — a probe: the step stays on the caller's own key
            logger.warning("waterfall: forager platform.ops unreadable, %s stays own-key "
                           "only", op, exc_info=True)
            return False
        return forager._served("forager_person", op, ops)
    return serves


def _apollo_args(inputs: dict) -> dict:
    args = {"linkedin_url": inputs.get("linkedin_url"), "email": inputs.get("email"),
            "first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
            "domain": inputs.get("domain"), "org_name": inputs.get("company_name")}
    return {k: v for k, v in args.items() if v}


def _lusha_contact(inputs: dict) -> dict:
    c = {"firstName": inputs.get("first_name"), "lastName": inputs.get("last_name"),
         "companyDomain": inputs.get("domain"), "companyName": inputs.get("company_name"),
         "linkedinUrl": inputs.get("linkedin_url"), "email": inputs.get("email")}
    return {k: v for k, v in c.items() if v}


def _lusha_first(res):
    first = _get(res, "results[0]")
    return None if not isinstance(first, dict) or first.get("error") else first


def _cognism_contact_args(inputs: dict) -> dict:
    args = {"first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
            "email": inputs.get("email"), "linkedin_url": inputs.get("linkedin_url"),
            "account_name": inputs.get("company_name"),
            "account_website": inputs.get("domain")}
    return {k: v for k, v in args.items() if v}


def _lemlist_args(inputs: dict, **actions) -> dict:
    args = {"linkedin_url": inputs.get("linkedin_url"), "email": inputs.get("email"),
            "first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
            "company_name": inputs.get("company_name"),
            "company_domain": inputs.get("domain")}
    return {**{k: v for k, v in args.items() if v}, **actions}


def _lemlist_first(res) -> dict:
    r = _get(res, "results[0]")
    return r if isinstance(r, dict) else {}


def _lemlist_done(res) -> bool:
    # `done` covers `not-found` too; a `done` with an empty digest carries a `warning`
    # asking to poll once more — not final yet.
    r = _lemlist_first(res)
    return bool(r.get("done")) and not (r.get("warning") and not r.get("found"))


_LEMLIST_COLLECT = Collect(
    "lemlist_enrich_result",
    lambda submitted: ({"enrichment_id": submitted["enrichment_id"]}
                       if isinstance(submitted, dict) and submitted.get("enrichment_id")
                       else None),
    _lemlist_done,
)


def _fullenrich_args(inputs: dict, field: str) -> dict:
    """FullEnrich takes a LinkedIn SLUG (not a URL) and/or the domain; one contact."""
    c = {"first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
         "domain": inputs.get("domain"), "company_name": inputs.get("company_name")}
    if inputs.get("linkedin_url"):
        c["linkedin_slug"] = _slug(inputs["linkedin_url"])
    return {"contacts": [{k: v for k, v in c.items() if v}], "enrich_fields": [field]}


def _fullenrich_profile(res) -> dict:
    p = _get(res, "profiles[0]")
    return p if isinstance(p, dict) else {}


_FULLENRICH_COLLECT = Collect(
    "fullenrich_result",
    lambda submitted: ({k: submitted[k] for k in ("enrichment_id", "submitted_at")
                        if submitted.get(k)}
                       if isinstance(submitted, dict) and submitted.get("enrichment_id")
                       else None),
    lambda res: isinstance(res, dict) and bool(res.get("done")),
)


def _dropcontact_args(inputs: dict) -> dict:
    c = {"first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
         "full_name": inputs.get("full_name"), "company": inputs.get("company_name"),
         "website": inputs.get("domain"), "linkedin": inputs.get("linkedin_url"),
         "email": inputs.get("email")}
    return {"contacts": [{k: v for k, v in c.items() if v}]}


def _dropcontact_profile(res) -> dict:
    p = _get(res, "profiles[0]")
    return p if isinstance(p, dict) else {}


_DROPCONTACT_COLLECT = Collect(
    "dropcontact_result",
    lambda submitted: ({"request_id": submitted["request_id"]}
                       if isinstance(submitted, dict) and submitted.get("request_id")
                       else None),
    lambda res: isinstance(res, dict) and bool(res.get("done")),
)

_APOLLO_REVEAL_COLLECT = Collect(
    "apollo_reveal_phone_result",
    # `{"matched": false}` at submission: no handle, nothing ordered — a miss.
    lambda submitted: ({"request_id": str(submitted["request_id"])}
                       if isinstance(submitted, dict) and submitted.get("request_id")
                       else None),
    lambda res: isinstance(res, dict) and bool(res.get("done")),
)

# Dropcontact identifies a person by LinkedIn URL, email, or name + company/website.
_DROPCONTACT_IDS = (("linkedin_url",), ("email",), _NAMES_AND_COMPANY, _NAMES_AND_DOMAIN)
# FullEnrich needs a LinkedIn slug OR a domain, with the name.
_FULLENRICH_IDS = (("linkedin_url",), _NAMES_AND_DOMAIN)


# An Apify run (the LinkedIn page of a company, its employees, a live crawl of a site):
# NO cost ceiling (`max_total_charge_usd`) by default — decided 19/10/2026: what bounds a
# run is its SIZE (pages crawled, results, profiles = the call's `limit`) and its TIME
# (`timeout_secs`, on Apify's side), pinned by `tests/waterfall/test_waterfall_moteur.py`.
# (Apify refuses a ceiling under $0.50 anyway — measured — so a ceiling here would be a
# backstop far above the cost, not a bound.) The caller may set one (`apify_max_usd`).
_APIFY_TIMEOUT_S = 25
#: A page's employees take ~27 s for a few full profiles (measured 19/10/2026; the
#: « short » mode is no faster and carries no title): the people waterfall's LAST step.
_APIFY_EMPLOYEES_TIMEOUT_S = 35


def _apify_items(res) -> list:
    """`apify_run_sync` answers the item LIST, or `{error, run_id, status, items}` for a
    run that ended TIMED-OUT/FAILED — whose partial items are still worth reading."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and isinstance(res.get("items"), list):
        return res["items"]
    return []


# lemlist's shared DATABASE (`lemlist_database`): no credits, one call of the key's search
# quota each (measured 21/10/2026) — `Step.quota_only`. Its filters are server-side; only
# the TEXT ones are used here: an autocomplete filter (a country, a company name) given a
# form it does not know empties the search without an error.

#: What lemlist's PEOPLE database drops from each record when asked (`excludes`): parts no
#: step reads — never a nested path. ⚠️ Never `experiences` (~90 % of a record): it is not
#: dropped but TRUNCATED (16 of 26 positions served, measured — the current one missing),
#: and the title is read from it.
_LEMLIST_EXCLUDES = ["education", "skills", "languages", "interests", "summary",
                     "shorthand_name_list", "lead_logo_url"]


def _lemlist_lead(rec: dict, at: Callable[[dict], bool]) -> tuple[Optional[dict], dict]:
    """What a people reading takes from a record of lemlist's people database: its CURRENT
    position (no end date) where `at` says — at the company asked (the people at a
    company), at the current employer (a person) —, the first of the profile when there
    are several (`order_in_profile` 0 is the first, not « no order »), None when there is
    none; and its email, a pattern guess, returned as `guessed` (Apollo's word for it)."""
    current = [e for e in rec.get("experiences") or []
               if isinstance(e, dict) and not e.get("date_to") and at(e)]
    position = min(current, key=lambda e: (99 if e.get("order_in_profile") is None
                                           else e["order_in_profile"])) if current else None
    email = rec.get("potential_email")
    return position, {"email": email, "email_status": "guessed" if email else None}


def _lemlist_company_args(inputs: dict) -> dict:
    """lemlist's company database, by website. A record carries the firmographics AND the
    technologies (with a confidence); ⚠️ also its last 6 months of job posts — ~0.5 MB on
    a 300-person company, measured: `op="companies"` takes no `excludes`."""
    return {"op": "companies", "page": 1, "size": 1,
            "filters": [{"filterId": "currentCompanyWebsiteUrl", "in": [inputs["domain"]],
                         "out": []}]}


def _lemlist_company_rec(res, inputs) -> Optional[dict]:
    """The record of the domain asked — a website filter may match a neighbour."""
    for rec in (_get(res, "results") or [])[:1]:
        if not isinstance(rec, dict):
            continue
        site = _domain(rec.get("company_domain") or rec.get("company_website_url"))
        if site == inputs.get("domain"):
            return rec
    return None


# --- Pipecorn: its contact lookup is asynchronous ----------------------------------

#: Pipecorn's own wait (`Step.poll`): first read after 2 s, then every 3 s (its reads are
#: free and exempt from its rate limit), 20 s at most (Julien, 29/10) — then the cascade
#: falls back on the next providers (`Step.falls_through`), with their full budget: at
#: worst 20 + 40 s, under the 120 s of an MCP call.
PIPECORN_POLL = (2.0, 3.0, 20.0)


def _pipecorn_collect(find: str) -> Collect:
    """The submission answers with an `enrichment_id`, read back (free) by the same tool
    with `op="result"` — and the same `find`, which bounds what the read bills."""
    return Collect(
        "pipecorn",
        lambda submitted: ({"op": "result", "enrichment_id": submitted["enrichment_id"],
                            "find": [find]}
                           if isinstance(submitted, dict) and submitted.get("enrichment_id")
                           else None),
        lambda res: isinstance(res, dict) and bool(res.get("done")),
    )
