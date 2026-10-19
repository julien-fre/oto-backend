"""Waterfall — native enrichment cascades: email, phone, company, person.

FOUR connectors, one tool each (`waterfall_email`, `waterfall_phone`, `waterfall_company`,
`waterfall_person` — readers only, so no `<ns>_act`, `tests/test_convention_act.py`),
served by this ONE module (`Connector.modules=("waterfall",)`, loaded once).

Each tool tries a list of existing connector tools, one after the other, and stops at the
FIRST that finds the value asked for. Nothing here talks to a provider: every step is an
ordinary call of the provider's own tool through `meta.executer_cible`, the seam
`oto_call` and the recipes use — so each step keeps ITS guards (connector activation for
the org/team of the call, credential cascade, access level, hosted-job tool list), ITS
row in `tool_calls` (and therefore its `quantity`/`key_mode`: the billing stays on the
provider's row, never on the waterfall's), and ITS namespace's redaction policy.

**Which steps, in which order** — three layers, the most specific wins:
1. the call's `providers=[…]` (validated: an unknown key is the caller's fault);
2. the `steps` setting of the waterfall's card — an org default set by an admin, a
   member's override, resolved by the same cascade as a key (`providers/waterfall_*.py`,
   `secret=False`: a setting, never a secret). An unknown key there is IGNORED and named
   on the receipt (`ignored_steps`): the person calling did not write it;
3. the default table below (`EMAIL_STEPS`…).
On the DEFAULT order only, **the providers the caller holds their OWN key for run first**
(user, team, org or tenant key — `access.credential_mode_for`, read once per call, before
spending anything) together with the FREE steps (French registry, the caller's own
LinkedIn session), the shared platform keys next, each group in the table's order. An
order someone WROTE (the call's, the setting's) is kept as written. Either way, a
provider with no key at all is skipped without being called.

**What a step's failure does**: nothing but move on — a connector not activated, a key
refused, an empty account, a rate limit, a provider error: each is recorded on the receipt
(`attempts`) with its code and message, and the next provider is tried. Only the caller's
OWN input faults raise.

**Asynchronous providers** (lemlist): the step submits, then polls the provider's own
collect tool (free) within a bounded wait. Not done in time → the step is `pending`, with
the exact collect call to make later, and the cascade moves on.

**One contact per call.** A list goes through a recipe (`oto_recipe`, `for_each` over the
rows of a table): the tools are declared read-only (`lecture.LECTURE`) for that reason —
they change nothing at any third party, even though their steps spend credits.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS
from starlette.concurrency import run_in_threadpool

from ..mcp_errors import McpError
from ..auth.hooks import current_user_sub_from_token
from .lecture import LECTURE, documente

logger = logging.getLogger(__name__)

# A response only counts as a hit if it carries a value of the right SHAPE: a provider
# that echoes the input back, or a sentence in an `email` field, is not a find.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_DIGITS = re.compile(r"\d")

#: Key modes that mean "the caller's OWN key" (`access.views.BYO_MODES`).
_OWN_KEY = ("user", "group", "org", "tenant")
#: …and those that mean "no usable key": the step is skipped without a call.
_NO_KEY = ("forbidden", "over_quota")

#: The whole cascade answers within this — under the 45 s of the REST tool-invoke path
#: (`capabilities/tools_me.py`), the tightest bound a caller meets (MCP: 120 s). A step
#: that would start after it is skipped (`time_budget`).
DEADLINE_S = 40.0
#: An asynchronous step polls for at most this long, first poll after `POLL_FIRST_S`.
POLL_BUDGET_S = 25.0
POLL_FIRST_S = 8.0
POLL_EVERY_S = 6.0


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


def _scalar(v) -> bool:
    return isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != ""


def _pick(obj, *paths):
    """The first path that leads to a non-empty SCALAR."""
    for p in paths:
        v = _get(obj, p)
        if _scalar(v):
            return v
    return None


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
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v).split("/", 1)[0].split("?", 1)[0]
    v = v.removeprefix("www.")
    return v if "." in v else None


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


def _company_inputs(company_name, domain, siren, country) -> dict:
    siren_digits = re.sub(r"\D", "", siren or "") or None
    if siren and (not siren_digits or len(siren_digits) != 9):
        raise _bad(f"`siren` must have 9 digits — got `{siren}`.")
    c = (country or "").strip().upper() or None
    return {"company_name": (company_name or "").strip() or None,
            "domain": _domain(domain), "siren": siren_digits,
            "fr": c in (None, "FR", "FRA", "FRANCE")}


# --- the engine --------------------------------------------------------------------

@dataclass(frozen=True)
class Collect:
    """The free collect call of an asynchronous provider: `tool`, its arguments built
    from the SUBMIT result (None = the submit gave no handle), and whether a collected
    result is final."""
    tool: str
    args: Callable[[object], Optional[dict]]
    done: Callable[[object], bool]


@dataclass(frozen=True)
class Step:
    """One provider in a cascade.

    `requires`: alternative groups of normalized inputs — the step runs if ALL the
    inputs of at least ONE group are present (`(("linkedin_url",), ("email",))` = a
    LinkedIn URL OR an email). `build` turns the inputs into the tool's arguments;
    `extract(result, inputs)` reads the SERVED result (of the collect call, for an
    asynchronous step) and returns the find, or None."""
    key: str
    tool: str
    requires: tuple[tuple[str, ...], ...]
    build: Callable[[dict], dict]
    extract: Callable[[object, dict], object]
    cost: str
    # What a skipped step tells the caller it needed, when the input keys alone would
    # not say it (an internal flag is not something a caller can give).
    needs: str = ""
    collect: Optional[Collect] = None
    # Costs the caller nothing (open data, their own session): ranks with their own keys.
    free: bool = False


def _runnable(step: Step, inputs: dict) -> bool:
    return any(all(inputs.get(k) for k in group) for group in step.requires)


def _missing(step: Step) -> str:
    if step.needs:
        return step.needs
    return " or ".join("+".join(g) for g in step.requires)


def _parse_steps(raw) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw or ""))
    return [str(i).strip().lower() for i in items if str(i).strip()]


def _carrier(tool: str) -> Optional[str]:
    """The vault carrier of the connector serving `tool` (`linkedin_aiark_person` →
    `aiark`, `fr_get` → `sirene`, `linkedin_unipile_profile` → `unipile`)."""
    from .. import providers
    from ..tool_visibility import namespace_of
    con = providers.connector_for_namespace(namespace_of(tool))
    return providers.credential_provider(con.name) if con is not None else None


def _configured_steps(ns: str) -> Optional[list[str]]:
    """The `steps` setting of the waterfall's card for the caller (member override >
    team > org > tenant), or None. SYNC — it reads the vault: off the loop."""
    from .. import access
    try:
        rc = access.resolve_credential(ns, want="byo", emit_on_failure=False)
    except McpError:
        return None  # nothing set anywhere: the default order
    return _parse_steps((rc.fields or {}).get("steps")) or None


def _key_modes(sub: Optional[str], tools: list[str]) -> dict[str, str]:
    """`tool → key mode` for each step, BEFORE anything is spent. SYNC (database)."""
    from .. import access, providers
    out: dict[str, str] = {}
    by_carrier: dict[str, str] = {}
    for tool in tools:
        carrier = _carrier(tool)
        if carrier is None or carrier not in providers.CREDENTIAL_PROVIDERS:
            out[tool] = "open"
            continue
        if carrier not in by_carrier:
            if not sub:
                by_carrier[carrier] = "unknown"
            else:
                try:
                    by_carrier[carrier] = access.credential_mode_for(sub, carrier)
                except Exception:  # noqa: BLE001 — a probe: the step's own call will judge
                    logger.warning("waterfall: key mode of %s unreadable", carrier,
                                   exc_info=True)
                    by_carrier[carrier] = "unknown"
        out[tool] = by_carrier[carrier]
    return out


def _plan(ns: str, steps: tuple[Step, ...], providers_arg: Optional[list[str]],
          sub: Optional[str]) -> tuple[list[Step], list[str], str, dict[str, str]]:
    """(ordered steps, ignored setting keys, where the order came from, key modes)."""
    by_key = {s.key: s for s in steps}
    ignored: list[str] = []
    if providers_arg is not None:
        keys = _parse_steps(providers_arg)
        if not keys:
            raise _bad("`providers` is empty — omit it to use your configured or the "
                       "default order.")
        unknown = [p for p in keys if p not in by_key]
        if unknown:
            raise _bad(f"Unknown provider(s) {unknown} — accepted here: {list(by_key)}.")
        source = "call"
    else:
        configured = _configured_steps(ns) or []
        ignored = [p for p in configured if p not in by_key]
        keys = [p for p in configured if p in by_key]
        source = "setting" if keys else "default"
        if not keys:
            keys = list(by_key)
    ordered = [by_key[k] for k in dict.fromkeys(keys)]
    modes = _key_modes(sub, [s.tool for s in ordered])
    if source == "default":
        # Own keys and free steps first, then shared keys — the sort is stable: each
        # group keeps the table's order. A WRITTEN order is not re-sorted.
        ordered.sort(key=lambda s: 0 if s.free or modes.get(s.tool) in _OWN_KEY else 1)
    return ordered, ignored, source, modes


async def _poll(mcp: FastMCP, sub, step: Step, submitted, deadline: float):
    """Collect an asynchronous step: (issue | None, collect args | None, finished)."""
    from . import meta
    args = step.collect.args(submitted)
    if not args:
        return None, None, True
    tool = await meta.resoudre_outil(mcp, step.collect.tool)
    if tool is None:
        return None, args, False
    stop = min(time.monotonic() + POLL_BUDGET_S, deadline)
    await asyncio.sleep(max(0.0, min(POLL_FIRST_S, stop - time.monotonic())))
    while True:
        issue = await meta.executer_cible(tool, sub, step.collect.tool,
                                          step.collect.tool, dict(args))
        if not issue.ok or issue.retenu or step.collect.done(issue.result):
            return issue, args, True
        if time.monotonic() + POLL_EVERY_S > stop:
            return issue, args, False
        await asyncio.sleep(POLL_EVERY_S)


async def _call(mcp: FastMCP, sub, tool_name: str, args: dict, attempt: dict):
    """One step's call: its `IssueCible`, or None after recording a skip."""
    from . import meta
    tool = await meta.resoudre_outil(mcp, tool_name)
    if tool is None:
        attempt.update(status="skipped", code="unknown_tool",
                       message=f"`{tool_name}` is not mounted on this instance")
        return None
    try:
        return await meta.executer_cible(tool, sub, tool_name, tool_name, dict(args))
    except McpError as e:
        # A GUARD refusal (connector not activated for this org/team, outside the
        # hosted job's tool list, access level): the caller's fault for a direct call,
        # a skipped step for a waterfall.
        data = getattr(e.error, "data", None)
        code = data.get("code") if isinstance(data, dict) else None
        attempt.update(status="skipped", code=code or "refused",
                       message=(e.error.message or "")[:300])
        return None


def _judge(issue, attempt: dict) -> bool:
    """Records a non-usable outcome on `attempt`; True if the result can be read."""
    if not issue.ok and issue.code == "not_found":
        # The provider answered "no such person/company" with a 404: a miss.
        attempt.update(status="no_match")
        return False
    if not issue.ok:
        attempt.update(status="failed", code=issue.code,
                       message=(issue.message or "")[:300])
        return False
    if issue.retenu:
        attempt.update(status="withheld", code="redaction_withheld",
                       message="the org's redaction policy withheld this result")
        return False
    return True


def _receipt(kind, found, provider, value, attempts, source, ignored, pending) -> dict:
    out = {"found": found, "provider": provider, kind: value, "attempts": attempts,
           "order_from": source}
    if ignored:
        out["ignored_steps"] = ignored
    if pending:
        out["pending"] = pending
    return out


async def _run_cascade(mcp: FastMCP, ns: str, kind: str, steps: tuple[Step, ...],
                       inputs: dict, providers_arg: Optional[list[str]],
                       dry_run: bool) -> dict:
    # The identity seam and the plan read the database: off the loop
    # (`docs/event-loop-perf.md`).
    sub = await run_in_threadpool(current_user_sub_from_token)
    order, ignored, source, modes = await run_in_threadpool(
        _plan, ns, steps, providers_arg, sub)
    if not any(_runnable(s, inputs) for s in order):
        raise _bad(
            f"No provider of this {kind} waterfall can run on these inputs. Each needs: "
            + "; ".join(f"{s.key} → {_missing(s)}" for s in order) + ".")
    deadline = time.monotonic() + DEADLINE_S
    attempts: list[dict] = []
    pending: list[dict] = []
    for step in order:
        mode = modes.get(step.tool, "unknown")
        attempt = {"provider": step.key, "tool": step.tool, "key": mode}
        attempts.append(attempt)
        if not _runnable(step, inputs):
            attempt.update(status="skipped", code="missing_input",
                           message=f"needs {_missing(step)}")
            continue
        if mode in _NO_KEY:
            attempt.update(status="skipped", code="no_key", message=(
                "no usable key for this provider — connect yours on its card"
                if mode == "forbidden" else "the shared key's quota is used up for today"))
            continue
        args = step.build(inputs)
        if dry_run:
            attempt.update(status="planned", arguments=args, cost=step.cost)
            continue
        if time.monotonic() >= deadline:
            attempt.update(status="skipped", code="time_budget",
                           message=f"the waterfall's {int(DEADLINE_S)} s budget is spent")
            continue
        issue = await _call(mcp, sub, step.tool, args, attempt)
        if issue is None or not _judge(issue, attempt):
            continue
        result = issue.result
        if step.collect is not None:
            collected, collect_args, finished = await _poll(mcp, sub, step, result,
                                                            deadline)
            if not finished:
                collect = {"tool": step.collect.tool, "arguments": collect_args}
                attempt.update(status="pending", collect=collect,
                               message="submitted, not finished in time — collect it "
                                       "later with `collect`")
                pending.append({"provider": step.key, **collect})
                continue
            if collected is None:
                attempt.update(status="no_match")
                continue
            if not _judge(collected, attempt):
                continue
            result = collected.result
        found = step.extract(result, inputs)
        if not found:
            attempt.update(status="no_match")
            continue
        attempt.update(status="found")
        return _receipt(kind, True, step.key, found, attempts, source, ignored, pending)
    if dry_run:
        return {"dry_run": True, "order_from": source, "attempts": attempts,
                **({"ignored_steps": ignored} if ignored else {})}
    return _receipt(kind, False, None, None, attempts, source, ignored, pending)


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


def _find_key(obj, names: tuple[str, ...], depth: int = 3):
    """The first non-empty SCALAR under one of `names` (case-insensitive), searched
    breadth-first down to `depth` — for providers whose response shape no test here
    pins (Cognism, Forager, Lusha's company block, lemlist's LinkedIn data)."""
    wanted = {n.lower() for n in names}
    level = [obj]
    for _ in range(depth + 1):
        nxt = []
        for node in level:
            if isinstance(node, dict):
                items = node.items()
            elif isinstance(node, list):
                items = enumerate(node)
            else:
                continue
            for k, v in items:
                if isinstance(k, str) and k.lower() in wanted and _scalar(v):
                    return v
                if isinstance(v, (dict, list)):
                    nxt.append(v)
        level = nxt
    return None


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


def _clean(d: dict, required: tuple[str, ...]) -> Optional[dict]:
    out = {k: v for k, v in d.items() if v not in (None, "", [], {})}
    return out if any(out.get(r) for r in required) else None


# Shared request builders --------------------------------------------------------------

_NAMES_AND_DOMAIN = ("first_name", "last_name", "domain")
_NAMES_AND_COMPANY = ("first_name", "last_name", "company_name")
_PERSON_IDS = (("linkedin_url",), _NAMES_AND_DOMAIN, _NAMES_AND_COMPANY)


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


# Email ------------------------------------------------------------------------------

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
                    key=lambda o: 0 if o.get("status") == "valid" else 1)
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


def _lemlist_email(res, inputs):
    found = _lemlist_first(res).get("found") or {}
    return isinstance(found, dict) and _email_find(
        _not_input(_emails_in(found.get("email")), inputs, "email"),
        found.get("email_status"))


EMAIL_STEPS: tuple[Step, ...] = (
    Step("hunter", "hunter_email_finder", (_NAMES_AND_DOMAIN,),
         lambda i: {"domain": i["domain"], "first_name": i["first_name"],
                    "last_name": i["last_name"]},
         _hunter_email, "1 Hunter credit"),
    Step("apollo", "apollo_match_person", _PERSON_IDS,
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
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["emails"]},
         _lusha_email, "Lusha credits per revealed email"),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_email=True),
         _lemlist_email, "lemlist credits per email found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT),
)


# Phone ------------------------------------------------------------------------------

def _kaspr_phone(res, inputs):
    return _phone_find(_deep(res, "phone", _phones_in))


def _forager_phone(res, inputs):
    return _phone_find(_deep({"x_phone": res}, "phone", _phones_in))


def _lusha_phone(res, inputs):
    first = _lusha_first(res)
    return first and _phone_find(_deep(first, "phone", _phones_in))


def _lemlist_phone(res, inputs):
    found = _lemlist_first(res).get("found") or {}
    return isinstance(found, dict) and _phone_find(_phones_in(found.get("phone")))


PHONE_STEPS: tuple[Step, ...] = (
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "with_phone": True,
                    "data_to_get": ["phone"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_phone, "1 Kaspr credit per phone"),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "phone_numbers",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_phone, "Forager credits on a hit only"),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["phones"]},
         _lusha_phone, "Lusha credits per revealed phone"),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_phone=True),
         _lemlist_phone, "lemlist credits per phone found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT),
)


# Company ----------------------------------------------------------------------------

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
    }, ("name",))


def _fr_get_company(res, inputs):
    if not isinstance(res, dict) or res.get("error"):
        return None
    return _fr_company(res.get("identity") or {})


def _norm_name(v: str) -> str:
    v = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", v))


def _same_company(inputs: dict, name, domain) -> bool:
    """A SEARCH always returns something: a result only counts when it IS the company
    asked for — same domain, or the asked name contained in its name. A waterfall that
    stopped on a namesake would hide the right answer further down."""
    if inputs.get("domain") and domain:
        return _domain(str(domain)) == inputs["domain"]
    asked = _norm_name(inputs.get("company_name") or "")
    got = _norm_name(str(name or ""))
    return bool(asked and got and (asked == got or f" {asked} " in f" {got} "))


def _fr_search_company(res, inputs):
    for ident in (_get(res, "results") or [])[:5]:
        if not isinstance(ident, dict):
            continue
        site = _domain(ident.get("site_internet"))
        if inputs.get("domain") and site == inputs["domain"]:
            return _fr_company(ident)
        names = [ident.get(k) for k in ("nom_complet", "nom_raison_sociale", "sigle")]
        if any(_same_company({"company_name": inputs.get("company_name")}, n, None)
               for n in names if n):
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
    }, ("name",))


def _aiark_company_args(inputs: dict) -> dict:
    if inputs.get("domain"):
        account = {"domain": {"any": {"include": [inputs["domain"]]}}}
    else:
        account = {"name": {"any": {"include": {"mode": "SMART",
                                                "content": [inputs["company_name"]]}}}}
    return {"op": "companies", "account": account, "size": 3}


def _aiark_company(res, inputs):
    for rec in (_get(res, "content") or [])[:3]:
        if not isinstance(rec, dict):
            continue
        name = _pick(rec, "summary.name", "name")
        domain = _domain(_pick(rec, "link.website", "link.domain", "summary.website",
                               "summary.domain", "website", "domain"))
        # A domain search returns the domain's company: no name to check. A name
        # search must name it.
        if not inputs.get("domain") and not _same_company(inputs, name, None):
            continue
        return _clean({
            "name": name,
            "domain": domain or inputs.get("domain"),
            "industry": _pick(rec, "summary.industry", "industry"),
            "employees": _pick(rec, "summary.staff.total", "staff.total"),
            "city": _pick(rec, "location.headquarter.city", "location.city"),
            "country": _pick(rec, "location.headquarter.country", "location.country"),
            "linkedin_url": _pick(rec, "link.linkedin"),
            "founded": _pick(rec, "summary.founded_year", "summary.founded"),
        }, ("name",))
    return None


def _theirstack_company(res, inputs):
    for rec in (_get(res, "data") or [])[:3]:
        if isinstance(rec, dict) and _same_company(inputs, rec.get("name"),
                                                   rec.get("domain")):
            return _clean({
                "name": rec.get("name"), "domain": _domain(rec.get("domain")),
                "employees": rec.get("employee_count"), "industry": rec.get("industry"),
                "linkedin_url": rec.get("linkedin_url"),
                "technologies": (rec.get("technology_names") or [])[:20] or None,
            }, ("name",))
    return None


def _cognism_company(res, inputs):
    for rec in (_get(res, "results") or [])[:3]:
        if not isinstance(rec, dict):
            continue
        name = _find_key(rec, ("name", "accountName", "companyName"), 2)
        domain = _find_key(rec, ("domain", "website", "websiteUrl"), 2)
        if not _same_company(inputs, name, domain):
            continue
        return _clean({
            "name": name, "domain": _domain(str(domain or "")),
            "industry": _find_key(rec, ("industry", "industries"), 2),
            "employees": _find_key(rec, ("size", "employees", "employeeCount",
                                         "headcount"), 2),
            "revenue": _find_key(rec, ("revenue", "annualRevenue"), 2),
            "country": _find_key(rec, ("country", "countryCode"), 3),
            "city": _find_key(rec, ("city",), 3),
            "linkedin_url": _find_key(rec, ("linkedinUrl", "linkedin_url", "linkedin"), 2),
        }, ("name",))
    return None


COMPANY_STEPS: tuple[Step, ...] = (
    Step("fr_get", "fr_get", (("siren",),),
         lambda i: {"siren": i["siren"]}, _fr_get_company, "free (French open data)",
         free=True),
    Step("fr_search", "fr_search", (("company_name", "fr"),),
         lambda i: {"query": i["company_name"], "per_page": 5},
         _fr_search_company, "free (French open data)",
         needs="company_name, and a French company (country omitted or FR)", free=True),
    Step("aiark", "linkedin_aiark_search", (("domain",), ("company_name",)),
         _aiark_company_args, _aiark_company, "AI Ark credits per company returned"),
    Step("apollo", "apollo_enrich_organization", (("domain",),),
         lambda i: {"domain": i["domain"]}, _apollo_company, "1 Apollo credit"),
    Step("theirstack", "theirstack_companies_search", (("domain",),),
         lambda i: {"extra": {"company_domain_or": [i["domain"]]}, "limit": 3},
         _theirstack_company, "~3 TheirStack credits per company"),
    Step("cognism", "cognism_enrich_account", (("domain",), ("company_name",)),
         lambda i: {k: v for k, v in {"name": i.get("company_name"),
                                      "domain": i.get("domain")}.items() if v},
         _cognism_company, "Cognism credits (per its contract)"),
)


# Person -----------------------------------------------------------------------------

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
        linkedin_url=p.get("linkedin_url") or inputs.get("linkedin_url"),
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


PERSON_STEPS: tuple[Step, ...] = (
    Step("linkedin", "linkedin_unipile_profile", (("linkedin_url",),),
         lambda i: {"op": "person", "identifier": _slug(i["linkedin_url"])},
         _generic_person, "free — your connected LinkedIn account (rate-limited)",
         free=True),
    Step("aiark", "linkedin_aiark_person", (("linkedin_url",),),
         lambda i: {"op": "export", "url": i["linkedin_url"]},
         _aiark_person, "AI Ark export credits"),
    Step("apollo", "apollo_match_person", _PERSON_IDS + (("email",),),
         _apollo_args, _apollo_person, "1 Apollo credit"),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "detail",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _generic_person, "1 Forager credit (own key only)"),
    Step("cognism", "cognism_enrich_contact", _PERSON_IDS + (("email",),),
         _cognism_contact_args,
         lambda res, i: _generic_person(_get(res, "results[0]"), i),
         "Cognism credits (per its contract)"),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS + (("email",),),
         # No `reveal`: the search alone carries the profile, and is the cheap mode.
         lambda i: {"contacts": [_lusha_contact(i)]},
         lambda res, i: _generic_person(_lusha_first(res), i), "Lusha search credits"),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, linkedin_enrichment=True),
         _lemlist_person, "lemlist credits (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT),
)


# --- the tools -----------------------------------------------------------------------

_ORDER_DOC = """**Order**: your `steps` setting on this connector's card if set (an org
        default, or your own override), kept as written; else the default order above,
        where the providers you hold your OWN key for (and the free steps) run first and
        the shared platform keys next. A provider with no usable key is skipped without
        being called. Each step
        uses THAT provider's key and is billed by it, exactly as a direct call of its
        tool — the waterfall adds no cost. A step whose inputs are missing, or whose
        connector is not activated for this org, is skipped too. Give every input you
        have. One contact per call; for a list, run it from a recipe (`for_each`)."""

_RECEIPT_DOC = """`attempts` says, step by step, what happened (found / no_match /
        skipped / failed / withheld / pending, with the code, the message and the key
        used); `order_from` says where the order came from (call / setting / default);
        `pending` lists the asynchronous steps to collect later."""

_COMMON_ARGS = """            providers: the steps to try for THIS call, in this order — keys
                from the list above. Overrides your setting. Never a tool name.
            dry_run: return the plan (order, key per step, arguments, skips) without
                calling anything. `planned` is not a promise: the connector's activation
                for your org is only checked by the real call."""


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=LECTURE)
    @documente(f"""Find ONE person's professional email, trying providers in turn and
        stopping at the FIRST that returns one (native waterfall).

        Steps (default order): hunter (name + domain) → apollo (LinkedIn URL, or name +
        domain / company) → aiark (LinkedIn URL) → kaspr (LinkedIn URL) → forager
        (LinkedIn URL) → lusha (LinkedIn URL, or name + domain / company) → lemlist
        (LinkedIn URL, or name + domain — asynchronous). The email is NOT verified here.

        {_ORDER_DOC}

        Returns `{{found, provider, email: {{email, all, status?}}, attempts,
        order_from}}` — {_RECEIPT_DOC}

        Args:
            first_name: the person's first name.
            last_name: the person's last name.
            full_name: alternative to first_name + last_name.
            domain: the company's domain (`acme.com`; a URL is accepted).
            company_name: the company's name, when the domain is unknown.
            linkedin_url: the person's LinkedIn profile URL.
{_COMMON_ARGS}
        """)
    async def waterfall_email(
        first_name: Optional[str] = None, last_name: Optional[str] = None,
        full_name: Optional[str] = None, domain: Optional[str] = None,
        company_name: Optional[str] = None, linkedin_url: Optional[str] = None,
        providers: Optional[list[str]] = None, dry_run: bool = False,
    ) -> dict:
        inputs = _person_inputs(first_name, last_name, full_name, domain,
                                company_name, linkedin_url, None)
        return await _run_cascade(mcp, "waterfall_email", "email", EMAIL_STEPS, inputs,
                                  providers, dry_run)

    @mcp.tool(annotations=LECTURE)
    @documente(f"""Find ONE person's phone number, trying providers in turn and stopping at
        the FIRST that returns one (native waterfall).

        Steps (default order): kaspr (LinkedIn URL) → forager (LinkedIn URL) → lusha
        (LinkedIn URL, or name + domain / company) → lemlist (LinkedIn URL, or name +
        domain — asynchronous). The LinkedIn URL opens the most steps. Phone credits
        cost more than email credits.

        {_ORDER_DOC}

        Returns `{{found, provider, phone: {{phone, all}}, attempts, order_from}}` —
        {_RECEIPT_DOC}

        Args:
            linkedin_url: the person's LinkedIn profile URL.
            first_name: the person's first name.
            last_name: the person's last name.
            full_name: alternative to first_name + last_name.
            domain: the company's domain (`acme.com`; a URL is accepted).
            company_name: the company's name, when the domain is unknown.
{_COMMON_ARGS}
        """)
    async def waterfall_phone(
        linkedin_url: Optional[str] = None, first_name: Optional[str] = None,
        last_name: Optional[str] = None, full_name: Optional[str] = None,
        domain: Optional[str] = None, company_name: Optional[str] = None,
        providers: Optional[list[str]] = None, dry_run: bool = False,
    ) -> dict:
        inputs = _person_inputs(first_name, last_name, full_name, domain,
                                company_name, linkedin_url, None)
        return await _run_cascade(mcp, "waterfall_phone", "phone", PHONE_STEPS, inputs,
                                  providers, dry_run)

    @mcp.tool(annotations=LECTURE)
    @documente(f"""Find ONE company's firmographics (legal name, SIREN, domain, city,
        industry / NAF, size, revenue, founding date, LinkedIn), trying sources in turn
        and stopping at the FIRST that identifies it (native waterfall).

        Steps (default order): fr_get (SIREN — French registry, free) → fr_search (name,
        French companies only, free) → aiark (domain or name) → apollo (domain) →
        theirstack (domain) → cognism (domain or name). A search result counts only if
        its domain matches, or its name contains the name asked: a namesake never stops
        the cascade. `employees_band` is the INSEE size band code, not a headcount.

        {_ORDER_DOC}

        Returns `{{found, provider, company: {{name, siren?, domain?, country?, city?,
        naf? | industry?, employees? | employees_band?, revenue?, founded?,
        linkedin_url?}}, attempts, order_from}}` — {_RECEIPT_DOC}

        Args:
            company_name: the company's name.
            domain: its website domain (`acme.com`; a URL is accepted).
            siren: its 9-digit SIREN (France).
            country: ISO country code; other than FR skips fr_search.
{_COMMON_ARGS}
        """)
    async def waterfall_company(
        company_name: Optional[str] = None, domain: Optional[str] = None,
        siren: Optional[str] = None, country: Optional[str] = None,
        providers: Optional[list[str]] = None, dry_run: bool = False,
    ) -> dict:
        inputs = _company_inputs(company_name, domain, siren, country)
        return await _run_cascade(mcp, "waterfall_company", "company", COMPANY_STEPS,
                                  inputs, providers, dry_run)

    @mcp.tool(annotations=LECTURE)
    @documente(f"""Find ONE person's current profile (title, company, seniority, location,
        LinkedIn), trying providers in turn and stopping at the FIRST that returns a
        title or a company (native waterfall).

        Steps (default order): linkedin (LinkedIn URL — your own connected LinkedIn
        account, free) → aiark (LinkedIn URL) → apollo (LinkedIn URL, email, or name +
        domain / company) → forager (LinkedIn URL) → cognism (same as apollo) → lusha
        (same as apollo) → lemlist (LinkedIn URL, or name + domain — asynchronous).

        {_ORDER_DOC}

        Returns `{{found, provider, person: {{title?, company_name?, company_domain?,
        full_name?, headline?, seniority?, city?, country?, linkedin_url?}},
        attempts, order_from}}` — {_RECEIPT_DOC}

        Args:
            linkedin_url: the person's LinkedIn profile URL.
            email: the person's email.
            first_name: the person's first name.
            last_name: the person's last name.
            full_name: alternative to first_name + last_name.
            domain: the company's domain (`acme.com`; a URL is accepted).
            company_name: the company's name, when the domain is unknown.
{_COMMON_ARGS}
        """)
    async def waterfall_person(
        linkedin_url: Optional[str] = None, email: Optional[str] = None,
        first_name: Optional[str] = None, last_name: Optional[str] = None,
        full_name: Optional[str] = None, domain: Optional[str] = None,
        company_name: Optional[str] = None,
        providers: Optional[list[str]] = None, dry_run: bool = False,
    ) -> dict:
        inputs = _person_inputs(first_name, last_name, full_name, domain,
                                company_name, linkedin_url, email)
        return await _run_cascade(mcp, "waterfall_person", "person", PERSON_STEPS,
                                  inputs, providers, dry_run)
