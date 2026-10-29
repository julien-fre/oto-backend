"""Pipecorn tool — contact enrichment (mobile phone, work email, personal email,
asynchronous), a person's profile, a company's firmographics, technologies and job
postings, and the account's balance.

Wraps `oto.tools.pipecorn.PipecornClient`. The key comes from the cascade (own key >
platform grant) via `access.resolve_api_key("pipecorn")`.

ONE tool, `pipecorn` (LECTURE): a lookup changes nothing at Pipecorn, it spends
credits — like any enrichment read here. No `pipecorn_act` (`tests/test_convention_act.py`).

**Contact lookups are asynchronous.** `op="enrich"` SUBMITS (Pipecorn queues it and
answers at once with an `enrichment_id`); `op="result"` reads it back (free), `pending`
until Pipecorn's own cascade of providers is done. `waterfall_phone` and
`waterfall_email` poll it with their own wait (`waterfall/fournisseurs.py`). The
profile, company, technologies and job postings (`person`, `company`, `stack`, `jobs`)
are synchronous.

**Billing facts** (Pipecorn credits, nothing on a miss): 3 a work email, 9 a personal
email, 30 a phone (only a MOBILE), 1 a profile or a company resolved, 3 a technology
stack (per request), 1 per job posting returned. `quantity` = those credits:
- a contact lookup: 0 at submission and while pending, the credits on the FINISHED
  result (`credits_spent`, bounded by the `find` asked — echoed by the submission and
  passed back); the billing keeps one line per `enrichment_id` (the result read's
  argument, `db.usage.BILLABLE_JOB_ARGS`), so a result read again bills nothing more;
- a synchronous lookup (`find`, `person`, `company`, `stack`, `jobs`): its own
  credits, on its call.
Any key: the billing filters on the key mode. `units` (the shared key's count — no
per-org cap, the org's credits bound it): 1 per submission or synchronous lookup, 0 on a
result read.

**The shared key** serves every lookup, the personal email included (Julien, 29/10:
Pipecorn wants the key owner's consent to it, in its settings — given on Tulina's
account), but not the balance nor the account (ours), refused BEFORE anything is spent.
A result is read with the key that submitted it.

**At scale** (a recipe, a waterfall's table mode): `op="find"` is the SAME contact
lookup on Pipecorn's synchronous endpoint — one call, billed on that call (no job). Every
lookup answers its finds as a LIST, `results`, which a recipe's `source.items` reads.

Client calls are written out (`client.submit_enrichment(…)`) so the version-skew
probe (`test_tools_client_methods_exist`) can check them.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection, session_org
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import LECTURE

_NAME = "pipecorn"

#: What a lookup may ask for, and what the shared key serves of it.
FINDS = ("phone", "email", "personal_email")
PLATFORM_FINDS = frozenset({"phone", "email", "personal_email"})

#: (connect, read) of a SYNCHRONOUS contact lookup (`op="find"`): Pipecorn runs its own
#: cascade inside the call; a waterfall's table mode gives a row's whole cascade 40 s.
FIND_TIMEOUT = (10, 30)

#: Why a lookup ended `failed`: credits ran out while it ran, nothing was found.
_FAILED = {
    "insufficient_credits": "the Pipecorn balance ran out while it ran",
    "workspace_limit_exceeded": "the key's weekly credit limit was reached while it ran",
}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _code(e) -> str:
    return e.body.get("code", "") if isinstance(e.body, dict) else ""


def _error(e) -> str:
    if isinstance(e.body, dict):
        return str(e.body.get("error") or e.body)
    return str(e.body or "")


def _upstream_message(e, is_platform: bool = False) -> str:
    """Turn a Pipecorn refusal into an actionable message, by status and `code`."""
    status = e.status_code
    if status == 402 or _code(e) == "insufficient_credits":
        if is_platform:
            return ("Pipecorn: the shared key's credit balance is empty (402) — nothing "
                    "was spent. Tell the instance's administrator; do not retry.")
        return ("Pipecorn: not enough credits left on your account for this lookup (402) "
                "— nothing was spent. Top up in Pipecorn, then retry.")
    if status == 403 and _code(e) == "workspace_limit_exceeded":
        return ("Pipecorn: this key's weekly credit limit for its workspace is reached "
                "(403) — a workspace admin raises it in Pipecorn, or wait for the week "
                "to reset.")
    if status == 401:
        return ("Pipecorn rejected the API key (401): copy it again from Settings → API "
                "keys; a rotated key stops working at once.")
    if status == 403:
        return f"Pipecorn refused this call (403): {_error(e)}"
    if status == 404:
        return ("Pipecorn has no such lookup for this key (404): check the "
                "`enrichment_id` — a lookup is read with the key that submitted it.")
    if status in (400, 422):
        return f"Pipecorn rejected the request ({status}): {_error(e)}"
    if status == 429:
        return "Pipecorn: too many requests (429) — retry in a few seconds."
    if status in (500, 502, 503, 504):
        return (f"Pipecorn could not finish the lookup (HTTP {status}) — nothing found "
                "was billed; retry later.")
    return f"Pipecorn refused the request (HTTP {status}): {_error(e)}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: the balance (`GET /credits`), free — it also says
    whether enrichment credits are left."""
    from oto.tools.pipecorn import PipecornClient
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        balance = PipecornClient(api_key=fields.get("key")).probe()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Pipecorn HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Pipecorn HTTP {e.status_code}: {e.body}")
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))
    left = ((balance or {}).get("credits") or {}).get("enrichment")
    if isinstance(left, (int, float)) and left <= 0:
        raise connector_verify.QuotaEpuise("Pipecorn: no enrichment credits left.")


def _finds_of(answer, finds) -> list[dict]:
    """What a contact lookup found, as a LIST — one entry per value, `kind` `phone`
    (with its line `type`), `email` or `personal_email` (with its `status`) — bounded by
    what was asked. A recipe writes one row per entry (`source.items: "results"`); a
    number the workspace suppressed ("Contacts not shared") is not one."""
    if not isinstance(answer, dict):
        return []
    out: list[dict] = []
    if "phone" in finds:
        for d in answer.get("phone_details") or []:
            number = d.get("number") if isinstance(d, dict) else None
            if isinstance(number, str) and number.startswith("+"):
                out.append({"kind": "phone", "value": number, "type": d.get("type")})
    for kind in ("email", "personal_email"):
        if kind in finds and answer.get(kind):
            out.append({"kind": kind, "value": answer[kind],
                        "status": answer.get(f"{kind}_status")})
    return out


def _resolved(answer, *keys) -> bool:
    """A profile or an account was found: the answer names it."""
    return isinstance(answer, dict) and any(answer.get(k) for k in keys)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.pipecorn import PipecornClient, credits_spent

    connector_verify.register(_NAME, _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client(op: str, finds: list[str]) -> tuple[PipecornClient, bool]:
        """`(client, is_platform)`. On the platform key, the balance and the account are
        refused (they are ours), naming what the shared key serves."""
        key, is_platform = access.resolve_api_key(_NAME)
        refused = (op in ("enrich", "find") and not set(finds) <= PLATFORM_FINDS) or \
            op in ("credits", "account")
        if is_platform and refused:
            from ..auth.hooks import current_user_sub_from_token
            from .. import links
            sub = current_user_sub_from_token()
            raise _bad(
                f"pipecorn op={op!r} (the balance) needs your own Pipecorn key: the shared "
                "key here covers every lookup, not the account's balance. Set your own key"
                f"{links.ou_poser_la_cle(sub, org=access.current_org(sub), connecteur=_NAME)}.")
        return PipecornClient(api_key=key), is_platform

    def _run(fn, is_platform: bool = False, miss=None):
        """The call; `miss` (when given) is what a 404 answers — nothing found, nothing
        spent — instead of a refusal."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            if miss is not None and e.status_code == 404:
                return miss
            raise _bad(_upstream_message(e, is_platform))

    def _spent(credits: int, is_platform: bool) -> None:
        """A synchronous lookup: what it cost, and one lookup on the shared key."""
        if is_platform:
            session_org.note_call_trace(quantity=credits, units=1)
        else:
            session_org.note_call_trace(quantity=credits)

    @mcp.tool(name="pipecorn", annotations=LECTURE)
    def pipecorn(
        op: Literal["enrich", "result", "find", "person", "company", "stack", "jobs",
                    "credits", "account"] = "enrich",
        find: Optional[list[Literal["phone", "email", "personal_email"]]] = None,
        linkedin_url: Optional[str] = None,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        domain: Optional[str] = None,
        company_name: Optional[str] = None,
        company_linkedin_url: Optional[str] = None,
        country_code: Optional[str] = None,
        phone_type: Optional[Literal["all", "mobile"]] = None,
        phone_country_codes: Optional[list[str]] = None,
        enrichment_id: Optional[str] = None,
        posted_within_days: Optional[Literal[7, 30, 60, 90, 180]] = None,
        limit: Optional[int] = None,
        job_title_keywords: Optional[list[str]] = None,
        job_country_codes: Optional[list[str]] = None,
        full_description: Optional[bool] = None,
    ) -> dict:
        """Pipecorn — a person's mobile phone, work email or personal email
        (asynchronous: submit then read; or synchronous: `find`); a person's profile; a
        company's firmographics, technologies and open job postings; the balance.

        You only pay for what is found, in Pipecorn credits: 3 a work email, 9 a personal
        email, 30 a phone (only when a MOBILE is found), 1 a profile or a company
        resolved, 3 a technology stack, 1 per job posting returned. A miss costs nothing.

        op="enrich" (default) — SUBMIT a contact lookup; answers at once with
          `enrichment_id`. `find`: any of `phone`, `email`, `personal_email` (default
          `["phone"]`). `phone` and `personal_email` need `linkedin_url`; `email` takes
          `linkedin_url`, or `firstname` + `lastname` + `domain` or `company_name`.
          `phone_type="mobile"` keeps looking until a provider has a mobile;
          `phone_country_codes=["FR"]` keeps phones to countries. `personal_email`
          needs the key owner's consent in Pipecorn's settings.
        op="result" — read it back: `enrichment_id`, and the same `find`. `done: false`
          while Pipecorn is still looking — read again in a few seconds (reads are
          free). Done: `email`, `email_status`, `phone`, `phone_details` [{number,
          type}], `personal_email`, `pipecorn_credits` (what it cost) and `results`.
        op="find" — the SAME lookup, synchronous: one call, the answer holds it (up to
          ~30 s). Same arguments as `enrich`. For a recipe or a table: `results` lists
          each value found — {kind: phone | email | personal_email, value, type or
          status} — one row each (`source.items: "results"`).
        op="person" — a profile in `results[0]`: title, positions with each company's
          domain, headline, location, tenure. `linkedin_url`, or `firstname` +
          `lastname` + `company_name`. No email or phone (that is `enrich` / `find`).
        op="company" — firmographics in `results[0]`: industry, headcount and its growth, department
          headcount, revenue range, headquarters, funding, web traffic. `domain`,
          `company_name` and/or `company_linkedin_url`; `country_code` narrows a name.
        op="stack" — the technologies a company uses, with a confidence. Same company
          arguments.
        op="jobs" — a company's open job postings (title, url, location, date,
          description, hiring team). Same company arguments; `posted_within_days` (7,
          30 by default, 60, 90, 180), `limit` (≤ 100: billed per job, so bound it),
          `job_title_keywords`, `job_country_codes`; each posting comes without its
          description unless `full_description=True` (then in full).
        op="credits" — the balance: `credits.enrichment` is what lookups spend.
        op="account" — the plan and the email behind the key.

        Not found: `found: false`, `results` empty. On the shared key (when it is
        granted to your org), every lookup — not the balance.
        For a contact, prefer the waterfalls (`waterfall_phone`, `waterfall_email`,
        `waterfall_person`, `waterfall_company`, `waterfall_tech`): Pipecorn is one of
        their steps.

        Examples:
          pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/",
                   phone_type="mobile")
          pipecorn(op="result", enrichment_id="…", find=["phone"])
          pipecorn(op="find", find=["phone", "personal_email"], linkedin_url="…")
          pipecorn(op="jobs", domain="acme.com", limit=20, job_country_codes=["FR"])
        """
        given = {"find": find, "linkedin_url": linkedin_url, "firstname": firstname,
                 "lastname": lastname, "domain": domain, "company_name": company_name,
                 "company_linkedin_url": company_linkedin_url, "country_code": country_code,
                 "phone_type": phone_type, "phone_country_codes": phone_country_codes,
                 "enrichment_id": enrichment_id, "posted_within_days": posted_within_days,
                 "limit": limit, "job_title_keywords": job_title_keywords,
                 "job_country_codes": job_country_codes,
                 "full_description": full_description}
        company = {"domain", "company_name", "company_linkedin_url"}
        contact = {"find", "linkedin_url", "firstname", "lastname", "domain",
                   "company_name", "phone_type", "phone_country_codes"}
        takes = {"enrich": contact, "find": contact,
                 "result": {"enrichment_id", "find"},
                 "person": {"linkedin_url", "firstname", "lastname", "company_name"},
                 "company": company | {"country_code"},
                 "stack": company,
                 "jobs": company | {"posted_within_days", "limit", "job_title_keywords",
                                    "job_country_codes", "full_description"},
                 }.get(op, set())
        stray = sorted(k for k, v in given.items() if v is not None and k not in takes)
        if stray:
            raise _bad(f"op='{op}' does not take {', '.join(f'`{k}`' for k in stray)}. "
                       "Nothing was done.")
        finds = list(dict.fromkeys(find or ["phone"]))
        client, is_platform = _client(op, finds)

        if op == "credits":
            return _run(lambda: client.get_credits())
        if op == "account":
            return _run(lambda: client.get_account_info())

        if op == "person":
            answer = _run(lambda: client.enrich_person(
                linkedin_url=linkedin_url, first_name=firstname, last_name=lastname,
                company_name=company_name), is_platform, miss={})
            found = _resolved(answer, "full_name", "title", "positions")
            _spent(1 if found else 0, is_platform)
            return {"found": found, "results": [answer] if found else []}
        if op == "company":
            answer = _run(lambda: client.enrich_account(
                domain=domain, name=company_name, linkedin_url=company_linkedin_url,
                country_code=country_code), is_platform, miss={})
            found = _resolved(answer, "company_name", "domain", "linkedin_url")
            _spent(1 if found else 0, is_platform)
            return {"found": found, "results": [answer] if found else []}
        if op == "stack":
            answer = _run(lambda: client.company_stack(
                domain=domain, name=company_name, linkedin_url=company_linkedin_url),
                is_platform, miss={})
            # Billed per REQUEST (3), found or not — a 404 aside.
            _spent(3 if answer else 0, is_platform)
            return {**answer, "found": bool(answer.get("technologies"))}
        if op == "jobs":
            answer = _run(lambda: client.company_hiring(
                domain=domain, name=company_name, linkedin_url=company_linkedin_url,
                posted_within_days=posted_within_days, limit=limit,
                job_title_keywords=job_title_keywords, job_country_codes=job_country_codes,
                full_description=full_description), is_platform, miss={"jobs": []})
            jobs = answer.get("jobs") if isinstance(answer, dict) else None
            _spent(len(jobs) if isinstance(jobs, list) else 0, is_platform)
            if full_description:
                return answer
            # The default view: each posting without its description snippet — title,
            # place, date, link and hiring team are what a list is read for.
            return output_projection.project(answer, items_path="jobs",
                                             item_drop=("description",))

        if op == "find":
            answer = _run(lambda: client.enrich_contact(
                finds, linkedin_url=linkedin_url, firstname=firstname,
                lastname=lastname, domain=domain, company_name=company_name,
                phone_type=phone_type, phone_country_codes=phone_country_codes,
                timeout=FIND_TIMEOUT), is_platform)
            # Synchronous: no job — billed on this call, what it found (`credits_spent`).
            spent = credits_spent(answer, finds)
            _spent(spent, is_platform)
            results = _finds_of(answer, finds)
            return {**answer, "found": bool(results), "results": results,
                    "pipecorn_credits": spent}

        if op == "enrich":
            submitted = _run(lambda: client.submit_enrichment(
                finds, linkedin_url=linkedin_url, firstname=firstname,
                lastname=lastname, domain=domain, company_name=company_name,
                phone_type=phone_type, phone_country_codes=phone_country_codes),
                is_platform)
            # Nothing is spent until the lookup finishes: a TRACED zero, not an absence.
            if is_platform:
                session_org.note_call_trace(quantity=0, units=1)
            else:
                session_org.note_call_trace(quantity=0)
            ident = submitted.get("enrichment_id") if isinstance(submitted, dict) else None
            if not ident:
                raise _bad("Pipecorn accepted the lookup but returned no enrichment_id: "
                           f"nothing can be read back ({str(submitted)[:200]}).")
            return {"enrichment_id": ident, "status": "pending", "find": finds,
                    "next_step": (f"Call pipecorn(op='result', enrichment_id='{ident}', "
                                  f"find={finds}) in a few seconds; reads are free.")}

        if not enrichment_id:
            raise _bad("op='result' needs the `enrichment_id` the submission returned.")
        answer = _run(lambda: client.get_enrichment(enrichment_id), is_platform)
        status = answer.get("status") if isinstance(answer, dict) else None
        if status == "failed":
            session_org.note_call_trace(quantity=0)
            code = answer.get("code") or ""
            why = _FAILED.get(code, f"Pipecorn stopped it ({code or 'no reason given'})")
            raise _bad(f"Pipecorn: lookup {enrichment_id} failed — {why}; nothing was "
                       "found or billed. Top up (or have the limit raised), then submit "
                       "it again.")
        if status != "finished":
            session_org.note_call_trace(quantity=0)
            return {"done": False, "enrichment_id": enrichment_id,
                    "status": status or "pending",
                    "next_step": "Still looking: read it again in a few seconds."}
        spent = credits_spent(answer, finds)
        if is_platform:
            session_org.note_call_trace(quantity=spent, units=0)
        else:
            session_org.note_call_trace(quantity=spent)
        results = _finds_of(answer, finds)
        return {**answer, "done": True, "found": bool(results), "results": results,
                "pipecorn_credits": spent}
