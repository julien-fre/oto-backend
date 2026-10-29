"""Pipecorn tool — waterfall contact enrichment (mobile phone, work email, personal
email) and the account's balance.

Wraps `oto.tools.pipecorn.PipecornClient`. The key comes from the cascade (own key >
platform grant) via `access.resolve_api_key("pipecorn")`.

ONE tool, `pipecorn` (LECTURE): a lookup changes nothing at Pipecorn, it spends
credits — like any enrichment read here. No `pipecorn_act` (`tests/test_convention_act.py`).

**Billing facts.** Pipecorn bills per result FOUND, in its credits: 3 a work email, 9 a
personal email, 30 a phone — a phone only when a MOBILE is found (`mobile` or
`fixed_or_mobile`); a miss costs nothing. The answer carries no cost: the client reads
it from what was found (`credits_spent`). Every lookup notes it as `quantity` (the
journal's billed quantity, any key: the billing filters on the key mode), and as
`units` on the platform key (the grant's daily quota, in Pipecorn credits).

**The shared key** serves a PHONE lookup only (the first step of `waterfall_phone`):
an email, a personal email (its consent is given by the key's owner, in Pipecorn's
settings) and the balance ask for the customer's own key, refused BEFORE anything is
spent. The grant's quota is checked for the worst case of the call (30 credits).

Client calls are written out (`_client(...).enrich_contact(…)`) so the version-skew
probe (`test_tools_client_methods_exist`) can check them.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

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

#: (connect, read) of a lookup. Pipecorn runs its own provider cascade inside the call;
#: `waterfall_phone` has 40 s for every step, so one lookup never holds it whole.
ENRICH_TIMEOUT = (10, 30)


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
    from oto.tools.pipecorn.client import CREDITS

    connector_verify.register(_NAME, _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client(op: str, finds: list[str]) -> tuple[PipecornClient, bool]:
        """`(client, is_platform)`. On the platform key, anything but a phone lookup is
        refused BEFORE a credit is spent, naming what the shared key serves; the grant's
        quota is checked for the call's worst case."""
        worst = sum(CREDITS[f] for f in finds) if op == "enrich" else 0
        key, is_platform = access.resolve_api_key(_NAME, units=max(worst, 1))
        if is_platform and (op != "enrich" or not set(finds) <= PLATFORM_FINDS):
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

    @mcp.tool(name="pipecorn", annotations=LECTURE)
    def pipecorn(
        op: Literal["enrich", "credits", "account"] = "enrich",
        find: Optional[list[Literal["phone", "email", "personal_email"]]] = None,
        linkedin_url: Optional[str] = None,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        domain: Optional[str] = None,
        company_name: Optional[str] = None,
        phone_type: Optional[Literal["all", "mobile"]] = None,
        phone_country_codes: Optional[list[str]] = None,
    ) -> dict:
        """Pipecorn — find a person's mobile phone, work email or personal email; the
        account's balance.

        Pipecorn queries many data providers one after the other in ONE synchronous
        call and stops at the first that answers. You only pay for what is found:
        3 Pipecorn credits a work email, 9 a personal email, 30 a phone — and a phone
        only when a MOBILE is found. A miss costs nothing.

        op="enrich" (default) — one person. `find`: any of `phone`, `email`,
          `personal_email` (default `["phone"]`).
          - `phone` and `personal_email` need `linkedin_url` (a profile URL).
          - `email` takes `linkedin_url`, or `firstname` + `lastname` + `domain` or
            `company_name`.
          - `phone_type="mobile"` keeps looking until a provider has a mobile and
            drops landlines; `phone_country_codes=["FR"]` keeps phones to countries.
          - `personal_email` needs the key owner's consent in Pipecorn's settings.
          Returns Pipecorn's answer — `email`, `email_status` (deliverable or
          catch-all), `phones` (E.164), `phone_details` [{number, type}],
          `phone_status` (valid | not_found), `personal_email` — plus
          `pipecorn_credits`, what the lookup cost. A miss is empty fields, not an
          error.
        op="credits" — the balance: `credits.enrichment` is what lookups spend.
        op="account" — the plan and the email behind the key.

        On the shared key (when it is granted to your org), phone lookups only;
        emails and the balance need your own key.

        Examples:
          pipecorn(find=["phone"], linkedin_url="https://www.linkedin.com/in/jane-doe/",
                   phone_type="mobile")
          pipecorn(find=["email"], firstname="Jane", lastname="Doe", domain="acme.com")
        """
        if op != "enrich":
            stray = sorted(k for k, v in {
                "find": find, "linkedin_url": linkedin_url, "firstname": firstname,
                "lastname": lastname, "domain": domain, "company_name": company_name,
                "phone_type": phone_type, "phone_country_codes": phone_country_codes,
            }.items() if v is not None)
            if stray:
                raise _bad(f"op='{op}' does not take {', '.join(f'`{k}`' for k in stray)}. "
                           "Nothing was done.")
            client, _ = _client(op, [])
            if op == "credits":
                return _run(lambda: client.get_credits())
            return _run(lambda: client.get_account_info())

        finds = list(dict.fromkeys(find or ["phone"]))
        client, is_platform = _client(op, finds)
        answer = _run(lambda: client.enrich_contact(
            finds, linkedin_url=linkedin_url, firstname=firstname, lastname=lastname,
            domain=domain, company_name=company_name, phone_type=phone_type,
            phone_country_codes=phone_country_codes, timeout=ENRICH_TIMEOUT), is_platform)
        spent = credits_spent(answer, finds)
        if is_platform:
            session_org.note_call_trace(quantity=spent, units=spent)
        else:
            session_org.note_call_trace(quantity=spent)
        if isinstance(answer, dict):
            answer = {**answer, "pipecorn_credits": spent}
        return answer
