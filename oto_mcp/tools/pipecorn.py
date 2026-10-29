"""Pipecorn tool — waterfall contact enrichment (mobile phone, work email, personal
email), asynchronous, and the account's balance.

Wraps `oto.tools.pipecorn.PipecornClient`. The key comes from the cascade (own key >
platform grant) via `access.resolve_api_key("pipecorn")`.

ONE tool, `pipecorn` (LECTURE): a lookup changes nothing at Pipecorn, it spends
credits — like any enrichment read here. No `pipecorn_act` (`tests/test_convention_act.py`).

**Asynchronous.** `op="enrich"` SUBMITS the lookup (Pipecorn queues it and answers at
once with an `enrichment_id`); `op="result"` reads it back (free), `pending` until
Pipecorn's own cascade of providers is done. `waterfall_phone` polls it like its other
asynchronous steps (`waterfall/telephone.py`).

**Billing facts.** Pipecorn bills per result FOUND, in its credits, when the lookup
finishes: 3 a work email, 9 a personal email, 30 a phone — a phone only when a MOBILE
is found (`mobile` or `fixed_or_mobile`); a miss costs nothing. Nothing says what it
cost: the client reads it from what was found (`credits_spent`, bounded by what was
asked — `find`, echoed by the submission and passed back to the result). `quantity`:
0 at submission and while pending, the credits on the FINISHED result — a result read
again bills nothing more, the billing keeps one line per `enrichment_id` (its call
argument, `db.usage.BILLABLE_JOB_ARGS`). Any key: the billing filters on the key mode.
`units` (the shared key's count, no per-org cap: the org's credits bound it): 1 per
submission, 0 on a result read.

**The shared key** serves a PHONE lookup only (the first step of `waterfall_phone`):
an email, a personal email (its consent is given by the key's owner, in Pipecorn's
settings) and the balance ask for the customer's own key, refused BEFORE anything is
spent. A result is read with the key that submitted it.

Client calls are written out (`client.submit_enrichment(…)`) so the version-skew
probe (`test_tools_client_methods_exist`) can check them.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, session_org
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from .lecture import LECTURE

_NAME = "pipecorn"

#: What a lookup may ask for, and what the shared key serves of it.
FINDS = ("phone", "email", "personal_email")
PLATFORM_FINDS = frozenset({"phone"})

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


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.pipecorn import PipecornClient, credits_spent

    connector_verify.register(_NAME, _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client(op: str, finds: list[str]) -> tuple[PipecornClient, bool]:
        """`(client, is_platform)`. On the platform key, a submission of anything but a
        phone, and the balance, are refused BEFORE a credit is spent, naming what the
        shared key serves. A result read is free on any key."""
        key, is_platform = access.resolve_api_key(_NAME)
        refused = (op == "enrich" and not set(finds) <= PLATFORM_FINDS) or \
            op in ("credits", "account")
        if is_platform and refused:
            from ..auth.hooks import current_user_sub_from_token
            from .. import links
            sub = current_user_sub_from_token()
            what = "the balance" if op != "enrich" else \
                ", ".join(f for f in finds if f not in PLATFORM_FINDS)
            raise _bad(
                f"pipecorn op={op!r} ({what}) needs your own Pipecorn key: the shared key "
                "here covers phone lookups only. Set your own key"
                f"{links.ou_poser_la_cle(sub, org=access.current_org(sub), connecteur=_NAME)}.")
        return PipecornClient(api_key=key), is_platform

    def _run(fn, is_platform: bool = False):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e, is_platform))

    def _finds(find) -> list[str]:
        return list(dict.fromkeys(find or ["phone"]))

    @mcp.tool(name="pipecorn", annotations=LECTURE)
    def pipecorn(
        op: Literal["enrich", "result", "credits", "account"] = "enrich",
        find: Optional[list[Literal["phone", "email", "personal_email"]]] = None,
        linkedin_url: Optional[str] = None,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        domain: Optional[str] = None,
        company_name: Optional[str] = None,
        phone_type: Optional[Literal["all", "mobile"]] = None,
        phone_country_codes: Optional[list[str]] = None,
        enrichment_id: Optional[str] = None,
    ) -> dict:
        """Pipecorn — find a person's mobile phone, work email or personal email
        (asynchronous: submit, then read the result); the account's balance.

        Pipecorn queries many data providers one after the other and stops at the first
        that answers. You only pay for what is found: 3 Pipecorn credits a work email,
        9 a personal email, 30 a phone — and a phone only when a MOBILE is found. A miss
        costs nothing.

        op="enrich" (default) — SUBMIT one person; answers at once with
          `enrichment_id`. `find`: any of `phone`, `email`, `personal_email` (default
          `["phone"]`).
          - `phone` and `personal_email` need `linkedin_url` (a profile URL).
          - `email` takes `linkedin_url`, or `firstname` + `lastname` + `domain` or
            `company_name`.
          - `phone_type="mobile"` keeps looking until a provider has a mobile and
            drops landlines; `phone_country_codes=["FR"]` keeps phones to countries.
          - `personal_email` needs the key owner's consent in Pipecorn's settings.
        op="result" — read it back: `enrichment_id`, and the same `find` as the
          submission. `done: false` while Pipecorn is still looking — read again in a
          few seconds (usually under a minute; reads are free). Done: `email`,
          `email_status` (deliverable or catch-all), `phone` (numbers),
          `phone_details` [{number, type}], `personal_email`, and `pipecorn_credits`,
          what the lookup cost. A miss is empty fields, not an error.
        op="credits" — the balance: `credits.enrichment` is what lookups spend.
        op="account" — the plan and the email behind the key.

        On the shared key (when it is granted to your org), phone lookups only;
        emails and the balance need your own key. For a person's phone across several
        providers, prefer `waterfall_phone`: it submits and reads this for you.

        Examples:
          pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/",
                   phone_type="mobile")
          pipecorn(op="result", enrichment_id="…", find=["phone"])
        """
        given = {"find": find, "linkedin_url": linkedin_url, "firstname": firstname,
                 "lastname": lastname, "domain": domain, "company_name": company_name,
                 "phone_type": phone_type, "phone_country_codes": phone_country_codes,
                 "enrichment_id": enrichment_id}
        takes = {"enrich": set(given) - {"enrichment_id"},
                 "result": {"enrichment_id", "find"}}.get(op, set())
        stray = sorted(k for k, v in given.items() if v is not None and k not in takes)
        if stray:
            raise _bad(f"op='{op}' does not take {', '.join(f'`{k}`' for k in stray)}. "
                       "Nothing was done.")
        finds = _finds(find)
        client, is_platform = _client(op, finds)

        if op == "credits":
            return _run(lambda: client.get_credits())
        if op == "account":
            return _run(lambda: client.get_account_info())

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
        return {**answer, "done": True, "pipecorn_credits": spent}
