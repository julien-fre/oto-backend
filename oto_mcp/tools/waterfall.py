"""Waterfall — native enrichment cascades: email, phone, company.

One tool, `waterfall(op=email|phone|company)` — a reader only, so no `waterfall_act`
(`tests/test_convention_act.py`). Each op tries a FIXED list of existing connector tools, one after the other, and stops
at the FIRST that finds the value asked for. Nothing here talks to a provider: every
step is an ordinary call of the provider's own tool through `meta.executer_cible`, the
seam `oto_call` and the recipes use — so each step keeps ITS guards (connector
activation for the org/team of the call, credential cascade, access level, hosted-job
tool list), ITS row in `tool_calls` (and therefore its `quantity`/`key_mode`: the
billing stays on the provider's row, never on the waterfall's), and ITS namespace's
redaction policy. The waterfall itself bills nothing.

**What a step's failure does**: nothing but move on. A connector not activated for the
org, a key that is not set, an empty account, a rate limit, a provider error — each is
recorded on the receipt (`attempts`) with its code and message, and the next provider is
tried. Only the caller's OWN input faults raise (no usable input at all, an unknown
provider key).

**Steps are a closed table** (`EMAIL_STEPS`, `PHONE_STEPS`, `COMPANY_STEPS`): a caller
reorders or narrows them with `providers=[…]`, never names an arbitrary tool — that is
what keeps a write (or the waterfall itself) out of a cascade.

**Synchronous providers only.** Submit-and-collect enrichers (Dropcontact, FullEnrich,
lemlist, Apollo's phone reveal) answer later, by polling or webhook: they cannot be one
step of a cascade that must answer within one call. They stay callable directly.

**One contact per call.** A list goes through a recipe (`oto_recipe`, `for_each` over the
rows of a table): the tool is declared read-only (`lecture.LECTURE`) for that
reason — they change nothing at any third party, even though their steps spend credits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS
from starlette.concurrency import run_in_threadpool

from ..mcp_errors import McpError
from ..auth.hooks import current_user_sub_from_token

from .lecture import LECTURE

# A tool's response only counts as a hit if it carries a value of the right SHAPE: a
# provider that echoes the input back, or a sentence in an `email` field, is not a find.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_DIGITS = re.compile(r"\d")


def _bad(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _first(*values):
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def _get(obj, path: str):
    """Dotted path with list indices (`a.b[0].c`); None as soon as it leads nowhere."""
    cur = obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _email(value) -> Optional[str]:
    if isinstance(value, dict):
        value = _first(value.get("email"), value.get("address"), value.get("value"))
    if not isinstance(value, str):
        return None
    value = value.strip().removeprefix("mailto:").strip()
    return value.lower() if _EMAIL_RE.match(value) else None


def _phone(value) -> Optional[str]:
    if isinstance(value, dict):
        value = _first(value.get("number"), value.get("phone"), value.get("value"),
                       value.get("sanitized_number"), value.get("raw_number"),
                       value.get("international"), value.get("phone_number"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(int(value))
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if len(_PHONE_DIGITS.findall(value)) >= 6 else None


def _emails_in(values) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    out: list[str] = []
    for v in values:
        e = _email(v)
        if e and e not in out:
            out.append(e)
    return out


def _phones_in(values) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    out: list[str] = []
    for v in values:
        p = _phone(v)
        if p and p not in out:
            out.append(p)
    return out


# --- inputs ------------------------------------------------------------------------

def _domain(value: Optional[str]) -> Optional[str]:
    """`https://www.Acme.com/x` and `jane@acme.com` → `acme.com`."""
    if not value:
        return None
    v = value.strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v).split("/", 1)[0].split("?", 1)[0]
    v = v.removeprefix("www.")
    return v or None


def _person_inputs(first_name, last_name, full_name, domain, company_name,
                   linkedin_url, email) -> dict:
    first_name = (first_name or "").strip() or None
    last_name = (last_name or "").strip() or None
    full_name = (full_name or "").strip() or None
    if full_name and not (first_name and last_name):
        parts = full_name.split()
        if len(parts) >= 2:
            first_name = first_name or parts[0]
            last_name = last_name or " ".join(parts[1:])
    if not full_name and first_name and last_name:
        full_name = f"{first_name} {last_name}"
    return {
        "first_name": first_name, "last_name": last_name, "full_name": full_name,
        "domain": _domain(domain) or _domain(email),
        "company_name": (company_name or "").strip() or None,
        "linkedin_url": (linkedin_url or "").strip() or None,
        "email": _email(email),
    }


# --- the engine --------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One provider in a cascade.

    `requires`: alternative groups of normalized inputs — the step runs if ALL the
    inputs of at least ONE group are present (`(("linkedin_url",), ("email",))` = a
    LinkedIn URL OR an email). `build` turns the inputs into the tool's arguments;
    `extract(result, inputs)` reads the SERVED result and returns the find, or None."""
    key: str
    tool: str
    requires: tuple[tuple[str, ...], ...]
    build: Callable[[dict], dict]
    extract: Callable[[object, dict], object]
    cost: str
    # What a skipped step tells the caller it needed, when the input keys alone would
    # not say it (an internal flag is not something a caller can give).
    needs: str = ""


def _runnable(step: Step, inputs: dict) -> bool:
    return any(all(inputs.get(k) for k in group) for group in step.requires)


def _missing(step: Step) -> str:
    if step.needs:
        return step.needs
    return " or ".join("+".join(g) for g in step.requires)


def _order(steps: tuple[Step, ...], providers: Optional[list[str]]) -> list[Step]:
    by_key = {s.key: s for s in steps}
    if providers is None:
        return list(steps)
    if not providers:
        raise _bad("`providers` is empty — omit it to use the default order.")
    unknown = [p for p in providers if p not in by_key]
    if unknown:
        raise _bad(f"Unknown provider(s) {unknown} — accepted here: {list(by_key)}.")
    return [by_key[p] for p in dict.fromkeys(providers)]


async def _run_cascade(mcp: FastMCP, kind: str, steps: tuple[Step, ...], inputs: dict,
                       providers: Optional[list[str]], dry_run: bool) -> dict:
    from . import meta
    order = _order(steps, providers)
    if not any(_runnable(s, inputs) for s in order):
        raise _bad(
            f"No provider of this {kind} waterfall can run on these inputs. Each needs: "
            + "; ".join(f"{s.key} → {_missing(s)}" for s in order) + ".")
    # The identity seam may read the database: off the loop (`docs/event-loop-perf.md`).
    sub = await run_in_threadpool(current_user_sub_from_token)
    attempts: list[dict] = []
    for step in order:
        attempt = {"provider": step.key, "tool": step.tool}
        attempts.append(attempt)
        if not _runnable(step, inputs):
            attempt.update(status="skipped", code="missing_input",
                           message=f"needs {_missing(step)}")
            continue
        args = step.build(inputs)
        if dry_run:
            attempt.update(status="planned", arguments=args, cost=step.cost)
            continue
        tool = await meta.resoudre_outil(mcp, step.tool)
        if tool is None:
            attempt.update(status="skipped", code="unknown_tool",
                           message=f"`{step.tool}` is not mounted on this instance")
            continue
        try:
            issue = await meta.executer_cible(tool, sub, step.tool, step.tool, dict(args))
        except McpError as e:
            # A GUARD refusal (connector not activated for this org/team, outside the
            # hosted job's tool list, access level): the caller's fault for a direct
            # call, a skipped step for a waterfall.
            data = getattr(e.error, "data", None)
            code = data.get("code") if isinstance(data, dict) else None
            attempt.update(status="skipped", code=code or "refused",
                           message=(e.error.message or "")[:300])
            continue
        if not issue.ok and issue.code == "not_found":
            # The provider answered "no such person/company" with a 404: a miss, not a
            # failure of the step.
            attempt.update(status="no_match")
            continue
        if not issue.ok:
            attempt.update(status="failed", code=issue.code,
                           message=(issue.message or "")[:300])
            continue
        if issue.retenu:
            attempt.update(status="withheld", code="redaction_withheld",
                           message="the org's redaction policy withheld this result")
            continue
        found = step.extract(issue.result, inputs)
        if not found:
            attempt.update(status="no_match")
            continue
        attempt.update(status="found")
        return {"found": True, "provider": step.key, kind: found, "attempts": attempts}
    receipt: dict = {"found": False, "provider": None, kind: None, "attempts": attempts}
    if dry_run:
        receipt["dry_run"] = True
        receipt.pop("found")
        receipt.pop("provider")
        receipt.pop(kind)
    return receipt


# --- reading a provider's answer -----------------------------------------------------

def _deep(obj, wanted: str, read: Callable[[object], list[str]],
          skip_keys: tuple[str, ...] = ("status", "qualification", "type", "required",
                                        "feedback", "verified", "score")) -> list[str]:
    """Every value under a key whose name contains `wanted` (`workEmail`,
    `professionalEmails`, `phones`…), read by `read` — for providers whose response
    nests contact points under varying names. A key that only QUALIFIES a contact
    point (`emailStatus`, `phoneType`) is not one."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                lk = str(k).lower()
                if wanted in lk and not any(s in lk for s in skip_keys):
                    for x in read(v):
                        if x not in out:
                            out.append(x)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(obj)
    return out


def _not_input(values: list[str], inputs: dict, key: str) -> list[str]:
    """A provider that echoes the contact point it was GIVEN has not found it."""
    given = (inputs.get(key) or "").lower()
    return [v for v in values if v.lower() != given]


def _email_find(emails: list[str], status: Optional[str] = None) -> Optional[dict]:
    if not emails:
        return None
    out = {"email": emails[0], "all": emails}
    if status:
        out["status"] = status
    return out


def _phone_find(phones: list[str]) -> Optional[dict]:
    return {"phone": phones[0], "all": phones} if phones else None


def _slug(linkedin_url: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#]+)", linkedin_url or "", re.I)
    return (m.group(1) if m else (linkedin_url or "").strip("/")).lower()


# Email ------------------------------------------------------------------------------

def _hunter_email(res, inputs):
    data = _get(res, "data") or {}
    return _email_find(_emails_in(data.get("email")),
                       _get(data, "verification.status"))


def _apollo_args(inputs: dict) -> dict:
    args = {"linkedin_url": inputs.get("linkedin_url"),
            "first_name": inputs.get("first_name"), "last_name": inputs.get("last_name"),
            "domain": inputs.get("domain"), "org_name": inputs.get("company_name")}
    return {k: v for k, v in args.items() if v}


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
                    key=lambda o: 0 if o.get("status") == "valid" else 1)
    emails = _emails_in([o.get("address") for o in ranked])
    return _email_find(emails, ranked[0].get("status") if emails else None)


def _kaspr_email(res, inputs):
    return _email_find(_not_input(_deep(res, "email", _emails_in), inputs, "email"))


def _forager_email(res, inputs):
    return _email_find(_not_input(_deep({"x_email": res}, "email", _emails_in),
                                  inputs, "email"))


def _lusha_contact(inputs: dict) -> dict:
    c = {"firstName": inputs.get("first_name"), "lastName": inputs.get("last_name"),
         "companyDomain": inputs.get("domain"), "companyName": inputs.get("company_name"),
         "linkedinUrl": inputs.get("linkedin_url")}
    return {k: v for k, v in c.items() if v}


def _lusha_first(res):
    first = _get(res, "results[0]")
    return None if not isinstance(first, dict) or first.get("error") else first


def _lusha_email(res, inputs):
    first = _lusha_first(res)
    return first and _email_find(_not_input(_deep(first, "email", _emails_in),
                                            inputs, "email"))


_NAMES_AND_DOMAIN = ("first_name", "last_name", "domain")
_NAMES_AND_COMPANY = ("first_name", "last_name", "company_name")

EMAIL_STEPS: tuple[Step, ...] = (
    Step("hunter", "hunter_email_finder", (_NAMES_AND_DOMAIN,),
         lambda i: {"domain": i["domain"], "first_name": i["first_name"],
                    "last_name": i["last_name"]},
         _hunter_email, "1 Hunter credit"),
    Step("apollo", "apollo_match_person",
         (("linkedin_url",), _NAMES_AND_DOMAIN, _NAMES_AND_COMPANY),
         _apollo_args, _apollo_email, "1 Apollo credit"),
    Step("aiark", "linkedin_aiark_person", (("linkedin_url",),),
         lambda i: {"op": "export", "url": i["linkedin_url"]},
         _aiark_email, "AI Ark export credits"),
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "data_to_get": ["workEmail"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_email, "1 Kaspr credit per email"),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "work_emails",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_email, "1 Forager credit (own key only)"),
    Step("lusha", "lusha_search_and_enrich",
         (("linkedin_url",), _NAMES_AND_DOMAIN, _NAMES_AND_COMPANY),
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["emails"]},
         _lusha_email, "Lusha credits per revealed email (own key only)"),
)


# Phone ------------------------------------------------------------------------------

def _kaspr_phone(res, inputs):
    return _phone_find(_deep(res, "phone", _phones_in))


def _aiark_phone(res, inputs):
    if not _get(res, "found"):
        return None
    data = _get(res, "data") or []
    flat = [x for row in data for x in (row if isinstance(row, list) else [row])]
    return _phone_find(_phones_in(flat))


def _forager_phone(res, inputs):
    return _phone_find(_deep({"x_phone": res}, "phone", _phones_in))


def _lusha_phone(res, inputs):
    first = _lusha_first(res)
    return first and _phone_find(_deep(first, "phone", _phones_in))


PHONE_STEPS: tuple[Step, ...] = (
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "with_phone": True,
                    "data_to_get": ["phone"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_phone, "1 Kaspr credit per phone"),
    Step("aiark", "linkedin_aiark_person",
         (("linkedin_url",), ("domain", "full_name")),
         lambda i: ({"op": "mobile", "linkedin": i["linkedin_url"]}
                    if i.get("linkedin_url")
                    else {"op": "mobile", "domain": i["domain"], "name": i["full_name"]}),
         _aiark_phone, "AI Ark mobile credits"),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "phone_numbers",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_phone, "Forager credits on a hit only"),
    Step("lusha", "lusha_search_and_enrich",
         (("linkedin_url",), _NAMES_AND_DOMAIN, _NAMES_AND_COMPANY),
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["phones"]},
         _lusha_phone, "Lusha credits per revealed phone (own key only)"),
)


# Company ----------------------------------------------------------------------------

def _clean(d: dict) -> Optional[dict]:
    out = {k: v for k, v in d.items() if v not in (None, "", [], {})}
    return out if out.get("name") else None


def _fr_company(ident: dict) -> Optional[dict]:
    siege = ident.get("siege") or {}
    return _clean({
        "name": ident.get("nom_complet") or ident.get("nom_raison_sociale"),
        "siren": ident.get("siren"),
        "domain": _domain(ident.get("site_internet")),
        "country": "FR",
        "city": siege.get("libelle_commune"),
        "address": siege.get("adresse"),
        "naf": ident.get("activite_principale"),
        # An INSEE size BAND code, not a headcount — named as such.
        "employees_band": ident.get("tranche_effectif_salarie"),
        "category": ident.get("categorie_entreprise"),
        "founded": ident.get("date_creation"),
    })


def _fr_get_company(res, inputs):
    if not isinstance(res, dict) or res.get("error"):
        return None
    return _fr_company(res.get("identity") or {})


def _norm_name(v: str) -> str:
    import unicodedata
    v = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", v))


def _fr_search_company(res, inputs):
    """The first result only counts when it IS the company asked for: same domain, or
    the asked name contained in its legal name. A search always returns SOMETHING — a
    waterfall that stopped on a namesake would hide the right answer further down."""
    for ident in (_get(res, "results") or [])[:5]:
        if not isinstance(ident, dict):
            continue
        site = _domain(ident.get("site_internet"))
        if inputs.get("domain") and site == inputs["domain"]:
            return _fr_company(ident)
        asked = _norm_name(inputs.get("company_name") or "")
        names = [_norm_name(ident.get(k) or "") for k in
                 ("nom_complet", "nom_raison_sociale", "sigle")]
        if asked and any(asked == n or f" {asked} " in f" {n} " for n in names if n):
            return _fr_company(ident)
    return None


def _apollo_company(res, inputs):
    org = _get(res, "organization")
    if not isinstance(org, dict):
        return None
    return _clean({
        "name": org.get("name"),
        "domain": _domain(org.get("primary_domain") or org.get("website_url")),
        "country": org.get("country"),
        "city": org.get("city"),
        "employees": org.get("estimated_num_employees"),
        "revenue": org.get("annual_revenue"),
        "industry": org.get("industry"),
        "founded": org.get("founded_year"),
        "linkedin_url": org.get("linkedin_url"),
    })


COMPANY_STEPS: tuple[Step, ...] = (
    Step("fr_get", "fr_get", (("siren",),),
         lambda i: {"siren": i["siren"]}, _fr_get_company, "free (French open data)"),
    Step("fr_search", "fr_search", (("company_name", "fr"),),
         lambda i: {"query": i["company_name"], "per_page": 5},
         _fr_search_company, "free (French open data)",
         needs="company_name, and a French company (country omitted or FR)"),
    Step("apollo", "apollo_enrich_organization", (("domain",),),
         lambda i: {"domain": i["domain"]}, _apollo_company, "1 Apollo credit"),
)


# --- the tools -----------------------------------------------------------------------

_OPS = ("email", "phone", "company")


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=LECTURE)
    async def waterfall(
        op: Literal["email", "phone", "company"],
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        full_name: Optional[str] = None,
        domain: Optional[str] = None,
        company_name: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        siren: Optional[str] = None,
        country: Optional[str] = None,
        providers: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> dict:
        """Native waterfall enrichment: find ONE value by trying several providers in
        turn, stopping at the FIRST that returns it.

        `op`:
        - **"email"** — a person's professional email. Default order: hunter (name +
          domain) → apollo (LinkedIn URL, or name + domain / company) → aiark (LinkedIn
          URL) → kaspr (LinkedIn URL) → forager (LinkedIn URL) → lusha (LinkedIn URL, or
          name + domain / company). The email is NOT verified here.
        - **"phone"** — a person's phone number. Default order: kaspr (LinkedIn URL) →
          aiark (LinkedIn URL, or domain + full name) → forager (LinkedIn URL) → lusha
          (LinkedIn URL, or name + domain / company). Phone credits cost more than
          email credits: narrow with `providers` to spend on one only.
        - **"company"** — a company's firmographics. Default order: fr_get (SIREN —
          French registry, free) → fr_search (name, French companies only, free; a
          result counts only if its domain matches or its legal name contains the name
          asked) → apollo (domain, 1 Apollo credit). `employees_band` is the INSEE
          size band code, not a headcount.

        A step whose inputs are missing is skipped; so is a provider not connected or
        not activated for this org. Each step uses THAT provider's key and is billed by
        it, exactly as a direct call of its tool would be — the waterfall adds no cost.
        Give every input you have: more inputs = more steps can run. One contact (or
        company) per call; for a list, run it from a recipe (`for_each` over a table).

        Returns `{found, provider, <op>: {...}, attempts}` — `attempts` says, step by
        step, what happened (found / no_match / skipped / failed / withheld, with the
        code and message).

        Args:
            op: what to find — "email", "phone" or "company".
            first_name: the person's first name (email, phone).
            last_name: the person's last name (email, phone).
            full_name: alternative to first_name + last_name (email, phone).
            domain: the company's domain (`acme.com`; a URL is accepted).
            company_name: the company's name.
            linkedin_url: the person's LinkedIn profile URL (email, phone).
            siren: the company's 9-digit SIREN, France (company).
            country: ISO country code (company); other than FR skips fr_search.
            providers: the steps to try, IN THIS ORDER — a subset of the step keys
                named above for this op. Omitted = all of them, default order. Never a
                tool name.
            dry_run: return the plan (which steps would run, with which arguments,
                which are skipped for missing input) without calling anything.
        """
        if op not in _OPS:
            raise _bad(f"Unknown op `{op}` — one of {list(_OPS)}.")
        if op == "company":
            siren_digits = re.sub(r"\D", "", siren or "") or None
            if siren and (not siren_digits or len(siren_digits) != 9):
                raise _bad(f"`siren` must have 9 digits — got `{siren}`.")
            c = (country or "").strip().upper() or None
            inputs = {"company_name": (company_name or "").strip() or None,
                      "domain": _domain(domain), "siren": siren_digits,
                      "fr": c in (None, "FR", "FRA", "FRANCE")}
            return await _run_cascade(mcp, "company", COMPANY_STEPS, inputs, providers,
                                      dry_run)
        inputs = _person_inputs(first_name, last_name, full_name, domain,
                                company_name, linkedin_url, None)
        steps = EMAIL_STEPS if op == "email" else PHONE_STEPS
        return await _run_cascade(mcp, op, steps, inputs, providers, dry_run)
