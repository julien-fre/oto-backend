"""The email cascade (`waterfall_email`): its steps, in their default order, and how
each provider's answer is read."""
from __future__ import annotations

from .etape import Step
from .fournisseurs import (_DROPCONTACT_COLLECT, _DROPCONTACT_IDS, _FULLENRICH_COLLECT,
                           _FULLENRICH_IDS, _LEMLIST_COLLECT, _NAMES_AND_DOMAIN,
                           _PERSON_IDS, _apollo_args, _forager_shared, _dropcontact_args,
                           _dropcontact_profile, _fullenrich_args, _fullenrich_profile,
                           _lemlist_args, _lemlist_first, _lusha_contact, _lusha_first)
from .valeurs import _deep, _email_find, _emails_in, _get, _not_input, _slug

def _hunter_email(res, inputs):
    data = _get(res, "data") or {}
    return _email_find(_emails_in(data.get("email")),
                       _get(data, "verification.status"))


def _apollo_email(res, inputs):
    person = _get(res, "person") or {}
    # A weak identifier makes Apollo create an EMPTY record (`_stub`): billed, not a find.
    if person.get("_stub"):
        return None
    return _email_find(_not_input(_emails_in(person.get("email")), inputs, "email"),
                       person.get("email_status"))


def _aiark_email(res, inputs):
    if not _get(res, "found"):
        return None
    out = _get(res, "email.output") or []
    # The verified addresses first: AI Ark rates each one.
    ranked = sorted((o for o in out if isinstance(o, dict)),
                    # AI Ark writes the status in capitals ("VALID").
                    key=lambda o: 0 if str(o.get("status", "")).lower() == "valid" else 1)
    emails = _emails_in([o.get("address") for o in ranked])
    return _email_find(emails, ranked[0].get("status") if emails else None)


def _kaspr_email(res, inputs):
    return _email_find(_not_input(_deep(res, "email", _emails_in), inputs, "email"))


def _forager_email(res, inputs):
    return _email_find(_not_input(_deep({"x_email": res}, "email", _emails_in),
                                  inputs, "email"))


def _lusha_email(res, inputs):
    first = _lusha_first(res)
    return first and _email_find(_not_input(_deep(first, "email", _emails_in),
                                            inputs, "email"))


def _fullenrich_email(res, inputs):
    return _email_find(_not_input(_emails_in(_fullenrich_profile(res).get("work_emails")),
                                  inputs, "email"))


def _dropcontact_email(res, inputs):
    """`email[]` items carry a `qualification` (`nominative@pro`, `invalid@invalid`…):
    an address Dropcontact itself calls invalid is not a find."""
    items = _dropcontact_profile(res).get("email") or []
    items = items if isinstance(items, list) else [items]
    good = [i for i in items
            if not (isinstance(i, dict) and "invalid" in str(i.get("qualification", "")))]
    status = next((i.get("qualification") for i in good if isinstance(i, dict)), None)
    return _email_find(_not_input(_emails_in(good), inputs, "email"), status)


def _lemlist_email(res, inputs):
    found = _lemlist_first(res).get("found") or {}
    return isinstance(found, dict) and _email_find(
        _not_input(_emails_in(found.get("email")), inputs, "email"),
        found.get("email_status"))


#: Synchronous steps first, by provider cost and yield; the asynchronous ones last (a
#: `pending` one stops the cascade), the slowest — FullEnrich, 30 s to 4 min — at the end.
EMAIL_STEPS: tuple[Step, ...] = (
    Step("hunter", "hunter_email_finder", (_NAMES_AND_DOMAIN,),
         lambda i: {"domain": i["domain"], "first_name": i["first_name"],
                    "last_name": i["last_name"]},
         _hunter_email, "1 Hunter credit",
         explains=(
             "Finds the address from the name and the company's domain, and says how sure "
             "it is — strongest where the company has a public email pattern.")),
    # AI Ark before Apollo: on a real list both found the same emails, AI Ark with the
    # whole profile — and Apollo bills its match even without an email.
    Step("aiark", "linkedin_aiark_person", (("linkedin_url",),),
         lambda i: {"op": "export", "url": i["linkedin_url"]},
         _aiark_email, "AI Ark export credits",
         explains=(
             "Reads the email attached to a LinkedIn profile — on a real list, mostly the "
             "same emails as Apollo.")),
    Step("apollo", "apollo_match_person", _PERSON_IDS,
         _apollo_args, _apollo_email, "1 Apollo credit",
         explains=(
             "Apollo's contact database — wide coverage; its match is billed even when it "
             "has no email.")),
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "data_to_get": ["workEmail"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_email, "1 Kaspr credit per email",
         explains="Kaspr's database, from a LinkedIn profile."),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["emails"]},
         _lusha_email, "Lusha credits per revealed email",
         explains="Lusha's database — from a LinkedIn profile, or name and company."),
    # The shared key serves Forager's phone lookups by default (`tools/forager.py::
    # platform_ops`, an instance setting): an email lookup runs on it only when the
    # instance opens `forager_person:work_emails` — else on the caller's own key only.
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "work_emails",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_email, "Forager credits on a hit only", own_key_only=True,
         shared_serves=_forager_shared("work_emails"), asks="work_emails",
         explains=("Forager's database, from a LinkedIn profile — your own key, or the "
                   "shared one where this instance opens email lookups.")),
    # The asynchronous ones: each may wait up to `appel.POLL_BUDGET_S` for its answer.
    Step("dropcontact", "dropcontact_enrich", _DROPCONTACT_IDS,
         _dropcontact_args, _dropcontact_email,
         "1 Dropcontact credit per email found (own key only; asynchronous)",
         collect=_DROPCONTACT_COLLECT,
         explains=(
             "Dropcontact finds and qualifies the address — answers late, so often "
             "collected after the call.")),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_email=True),
         _lemlist_email, "lemlist credits per email found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT,
         explains=(
             "lemlist's own enrichment behind one call — your key only; answers within ~25 "
             "s.")),
    Step("fullenrich", "fullenrich_enrich_linkedin", _FULLENRICH_IDS,
         lambda i: _fullenrich_args(i, "contact.work_emails"), _fullenrich_email,
         "1 FullEnrich credit per work email found (asynchronous, ~30 s to 4 min)",
         collect=_FULLENRICH_COLLECT, asks="work_emails",
         explains=(
             "FullEnrich runs its own waterfall of providers — slow (30 s to 4 min), "
             "usually collected later.")),
)
