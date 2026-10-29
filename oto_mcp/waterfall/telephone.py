"""The phone cascade (`waterfall_phone`): its steps, in their default order, and how
each provider's answer is read. No AI Ark: it sells no phone."""
from __future__ import annotations

from .etape import Step
from .fournisseurs import (_APOLLO_REVEAL_COLLECT, _DROPCONTACT_COLLECT,
                           _DROPCONTACT_IDS, _FULLENRICH_COLLECT, _FULLENRICH_IDS,
                           _LEMLIST_COLLECT, _NAMES_AND_DOMAIN, _PERSON_IDS, _apollo_args,
                           _dropcontact_args, _dropcontact_profile, _fullenrich_args,
                           _fullenrich_profile, _lemlist_args, _lemlist_first,
                           _lusha_contact, _lusha_first, PIPECORN_POLL,
                           _pipecorn_collect)
from .valeurs import _deep, _get, _phone_find, _phones_in, _slug

def _pipecorn_phone(res, inputs):
    # Asked with `phone_type="mobile"`: every number left is a mobile (or a number that
    # cannot be told from one), and one the workspace suppressed reads "Contacts not
    # shared" — `_phone` drops it. The result lists the numbers under `phone`.
    if not isinstance(res, dict):
        return None
    numbers = [d.get("number") for d in res.get("phone_details") or [] if isinstance(d, dict)]
    return _phone_find(_phones_in(numbers or res.get("phone") or res.get("phones") or []))


def _kaspr_phone(res, inputs):
    return _phone_find(_deep(res, "phone", _phones_in))


def _forager_phone(res, inputs):
    return _phone_find(_deep({"x_phone": res}, "phone", _phones_in))


def _lusha_phone(res, inputs):
    first = _lusha_first(res)
    return first and _phone_find(_deep(first, "phone", _phones_in))


def _fullenrich_phone(res, inputs):
    return _phone_find(_phones_in(_fullenrich_profile(res).get("phones")))


def _dropcontact_phone(res, inputs):
    p = _dropcontact_profile(res)
    return _phone_find(_phones_in([p.get("mobile_phone"), p.get("phone")]))


def _apollo_reveal_phone(res, inputs):
    if not isinstance(res, dict) or res.get("delivered") is False:
        return None
    people = _get(res, "result.webhook_result.people") or []
    numbers = [n for p in people if isinstance(p, dict) for n in (p.get("phone_numbers") or [])]
    # Mobiles first, then direct dials; a number flagged do-not-call is still returned
    # (the caller decides), but never ahead of one that is not.
    def rank(n):
        if not isinstance(n, dict):
            return (2, 1)
        kind = str(n.get("type_cd") or n.get("type") or "")
        return (0 if "mobile" in kind else 1, 1 if n.get("dnc_status_cd") else 0)
    return _phone_find(_phones_in(sorted(numbers, key=rank)))


def _lemlist_phone(res, inputs):
    found = _lemlist_first(res).get("found") or {}
    return isinstance(found, dict) and _phone_find(_phones_in(found.get("phone")))


#: Synchronous steps first, by provider cost and yield; the asynchronous ones last (a
#: `pending` one stops the cascade): Apollo's reveal on the caller's own key, then the
#: slow ones — FullEnrich, 30 s to 4 min, at the end. EXCEPT Pipecorn, which heads the
#: table although it is asynchronous (Julien, 29/10): one lookup runs its own cascade of
#: providers and bills only a mobile found. It does not stop the cascade: not done
#: within its 20 s, the next providers are tried, and it is read once more at the end.
#: The table mode leaves it out with the other asynchronous steps
#: (`tools/waterfall_lignes.py`). The default order still puts the caller's own keys
#: ahead of a shared one (`ordre._plan`).
PHONE_STEPS: tuple[Step, ...] = (
    Step("pipecorn", "pipecorn", (("linkedin_url",),),
         lambda i: {"op": "enrich", "find": ["phone"],
                    "linkedin_url": i["linkedin_url"], "phone_type": "mobile"},
         _pipecorn_phone,
         "30 Pipecorn credits per mobile found, nothing on a miss (asynchronous)",
         collect=_pipecorn_collect("phone"), asks="phones", poll=PIPECORN_POLL,
         falls_through=True,
         explains=("Pipecorn runs its own cascade of phone providers — mobiles only, "
                   "from a LinkedIn profile; waited up to 20 s, then the other "
                   "providers are tried.")),
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "with_phone": True,
                    "data_to_get": ["phone"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_phone, "1 Kaspr credit per phone",
         explains="Kaspr's phone numbers, from a LinkedIn profile."),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["phones"]},
         _lusha_phone, "Lusha credits per revealed phone",
         explains="Lusha's phone numbers — from a LinkedIn profile, or name and company."),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "phone_numbers",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_phone, "Forager credits on a hit only", asks="phone_numbers",
         explains="Forager's phone numbers, from a LinkedIn profile."),
    Step("apollo", "apollo_reveal_phone", _PERSON_IDS,
         _apollo_args, _apollo_reveal_phone,
         "~9 Apollo credits per reveal (own key only; asynchronous)",
         collect=_APOLLO_REVEAL_COLLECT, own_key_only=True,
         explains="Apollo's phone reveal — your own Apollo key only; asynchronous."),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_phone=True),
         _lemlist_phone, "lemlist credits per phone found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT,
         explains="lemlist's enrichment — your key only; answers after a short wait."),
    Step("dropcontact", "dropcontact_enrich", _DROPCONTACT_IDS,
         _dropcontact_args, _dropcontact_phone,
         "Dropcontact credits (own key only; asynchronous)",
         collect=_DROPCONTACT_COLLECT,
         explains="Dropcontact's phone enrichment — asynchronous."),
    Step("fullenrich", "fullenrich_enrich_linkedin", _FULLENRICH_IDS,
         lambda i: _fullenrich_args(i, "contact.phones"), _fullenrich_phone,
         "10 FullEnrich credits per phone found (asynchronous, ~30 s to 4 min)",
         collect=_FULLENRICH_COLLECT, asks="phones",
         explains=(
             "FullEnrich runs its own waterfall of providers — usually collected later.")),
)
