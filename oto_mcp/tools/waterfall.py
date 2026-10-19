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
    # What a screen calls the step, when the connector's own label would not tell two
    # steps apart (the French registry by SIREN, and by name).
    label: str = ""
    # A COMPLETION step: it does not compete for the find, it fills ONE field the find
    # still lacks (`linkedin_url`), after the cascade — see `_run_cascade`.
    fills: str = ""
    # ONE row, several ways in (the French registry by SIREN, else by name): the variants
    # run in order, each when its own inputs are there, until one finds the value. The
    # receipt says which one answered (`via`). A screen shows one step.
    variants: tuple["Step", ...] = ()
    # A find too thin to stop on (Apollo's people search: every last name masked): kept
    # as the FALLBACK while the cascade looks further, returned only if nothing better
    # comes. The receipt marks the step `weak`.
    weak: Optional[Callable[[object], bool]] = None
    # The provider's tool refuses Tulina's shared key for this gesture (Apollo's phone
    # reveal): on any key but the caller's own, the step is skipped BEFORE the call, and
    # a screen lists it with the ones to connect.
    own_key_only: bool = False


def _payload(issue):
    """What the step's tool RETURNED, out of its served `ToolResult` (structured content,
    else the JSON of its first block) — the same reading as the recipes
    (`redaction.extract_payload`). A tool that returns a list is served wrapped
    (`{"result": [...]}`): unwrapped here, so an extractor reads what the tool wrote."""
    from .. import redaction
    data = redaction.extract_payload(issue.result)
    if data is None and isinstance(issue.result, (dict, list)):
        data = issue.result
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


def _runnable(step: Step, inputs: dict) -> bool:
    if step.variants:
        return any(_runnable(v, inputs) for v in step.variants)
    return any(all(inputs.get(k) for k in group) for group in step.requires)


def _missing(step: Step) -> str:
    if step.needs:
        return step.needs
    if step.variants:
        return " or ".join(_missing(v) for v in step.variants)
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
        if not issue.ok or issue.retenu or step.collect.done(_payload(issue)):
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


async def _run_step(mcp: FastMCP, sub, step: Step, mode: str, inputs: dict,
                    dry_run: bool, deadline: float, attempt: dict, pending: list):
    """Run ONE step and record what happened on `attempt`; return its find, or None."""
    if step.variants:
        if not _runnable(step, inputs):
            attempt.update(status="skipped", code="missing_input",
                           message=f"needs {_missing(step)}")
            return None
        tried: list[dict] = []
        for v in step.variants:
            if not _runnable(v, inputs):
                continue
            part = {"via": v.key, "tool": v.tool}
            tried.append(part)
            found = await _run_step(mcp, sub, v, mode, inputs, dry_run,
                                    deadline, part, pending)
            if found is not None:
                attempt.update(status="found", via=v.key, tool=v.tool, tried=tried)
                return found
        last = tried[-1] if tried else {}
        attempt.update({k: v for k, v in last.items() if k not in ("via", "tool")},
                       tried=tried)
        if dry_run:
            attempt.update(status="planned", arguments=last.get("arguments"),
                           cost=step.cost, tried=tried)
        return None
    if not _runnable(step, inputs):
        attempt.update(status="skipped", code="missing_input",
                       message=f"needs {_missing(step)}")
        return None
    if step.own_key_only and mode not in _OWN_KEY and mode != "unknown":
        attempt.update(status="skipped", code="own_key_required",
                       message="this step needs your own key for this provider — Tulina's "
                               "shared key does not serve it")
        return None
    if mode in _NO_KEY:
        attempt.update(status="skipped", code="no_key", message=(
            "no usable key for this provider — connect yours on its card"
            if mode == "forbidden" else "the shared key's quota is used up for today"))
        return None
    args = step.build(inputs)
    if dry_run:
        attempt.update(status="planned", arguments=args, cost=step.cost)
        return None
    if time.monotonic() >= deadline:
        attempt.update(status="skipped", code="time_budget",
                       message=f"the waterfall's {int(DEADLINE_S)} s budget is spent")
        return None
    issue = await _call(mcp, sub, step.tool, args, attempt)
    if issue is None or not _judge(issue, attempt):
        return None
    result = _payload(issue)
    if step.collect is not None:
        collected, collect_args, finished = await _poll(mcp, sub, step, result, deadline)
        if not finished:
            collect = {"tool": step.collect.tool, "arguments": collect_args}
            attempt.update(status="pending", collect=collect,
                           message="submitted, not finished in time — collect it "
                                   "later with `collect`")
            pending.append({"provider": step.key, **collect})
            return None
        if collected is None:
            attempt.update(status="no_match")
            return None
        if not _judge(collected, attempt):
            return None
        result = _payload(collected)
    found = step.extract(result, inputs)
    if not found:
        attempt.update(status="no_match")
        return None
    attempt.update(status="found")
    return found


async def _run_cascade(mcp: FastMCP, ns: str, kind: str, steps: tuple[Step, ...],
                       inputs: dict, providers_arg: Optional[list[str]],
                       dry_run: bool) -> dict:
    """The cascade: the first step that finds the value wins. Then the COMPLETION
    steps (`Step.fills`, e.g. a company's LinkedIn page): each runs only while the
    value still lacks the field it fills, and merges what it finds — a page found
    when nothing else identified the company still makes a (thin) find."""
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
    found, winner = None, None
    fallback, fallback_step = None, None
    for step in order:
        if step.fills:
            continue
        if found is not None:
            break       # the receipt ends at the winner, as it always has
        attempt = {"provider": step.key, "tool": step.tool,
                   "key": modes.get(step.tool, "unknown")}
        attempts.append(attempt)
        got = await _run_step(mcp, sub, step, attempt["key"], inputs, dry_run,
                              deadline, attempt, pending)
        if got is not None and step.weak is not None and step.weak(got):
            attempt.update(status="weak")
            if fallback is None:
                fallback, fallback_step = got, step.key
            continue
        found = got
        winner = step.key if found is not None else None
    if found is None and fallback is not None:
        found, winner = fallback, fallback_step
    for step in order:
        if not step.fills:
            continue
        if isinstance(found, dict) and found.get(step.fills):
            continue    # the field is there: nothing to fill, nothing to say
        attempt = {"provider": step.key, "tool": step.tool,
                   "key": modes.get(step.tool, "unknown"), "fills": step.fills}
        attempts.append(attempt)
        extra = await _run_step(mcp, sub, step, attempt["key"], inputs, dry_run,
                                deadline, attempt, pending)
        if extra:
            found = {**(found or {}), **{k: v for k, v in extra.items()
                                          if not (found or {}).get(k)}}
            winner = winner or step.key
    if dry_run:
        return {"dry_run": True, "order_from": source, "attempts": attempts,
                **({"ignored_steps": ignored} if ignored else {})}
    return _receipt(kind, found is not None, winner, found, attempts, source, ignored,
                    pending)


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
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["emails"]},
         _lusha_email, "Lusha credits per revealed email"),
    # The asynchronous ones next: each may wait up to `POLL_BUDGET_S` for its answer.
    Step("dropcontact", "dropcontact_enrich", _DROPCONTACT_IDS,
         _dropcontact_args, _dropcontact_email,
         "1 Dropcontact credit per email found (own key only; asynchronous)",
         collect=_DROPCONTACT_COLLECT),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_email=True),
         _lemlist_email, "lemlist credits per email found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT),
    Step("fullenrich", "fullenrich_enrich_linkedin", _FULLENRICH_IDS,
         lambda i: _fullenrich_args(i, "contact.work_emails"), _fullenrich_email,
         "1 FullEnrich credit per work email found (asynchronous, ~30 s to 4 min)",
         collect=_FULLENRICH_COLLECT),    # Forager last: on Tulina's key an email lookup is ◎ 11 — twice a FullEnrich email.
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "work_emails",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_email, "1 Forager credit (own key only)"),

)


# Phone ------------------------------------------------------------------------------

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


PHONE_STEPS: tuple[Step, ...] = (
    Step("kaspr", "kaspr_enrich_linkedin", (("linkedin_url",),),
         lambda i: {"linkedin_id": i["linkedin_url"], "with_phone": True,
                    "data_to_get": ["phone"],
                    **({"name": i["full_name"]} if i.get("full_name") else {})},
         _kaspr_phone, "1 Kaspr credit per phone"),
    Step("lusha", "lusha_search_and_enrich", _PERSON_IDS,
         lambda i: {"contacts": [_lusha_contact(i)], "reveal": ["phones"]},
         _lusha_phone, "Lusha credits per revealed phone"),
    # Apollo's reveal runs on your own key only (0 Tulina credits), before the ◎ 33
    # Forager lookup; the slow ones last — FullEnrich (◎ 60 per phone found) at the end.
    Step("apollo", "apollo_reveal_phone", _PERSON_IDS,
         _apollo_args, _apollo_reveal_phone,
         "~9 Apollo credits per reveal (own key only; asynchronous)",
         collect=_APOLLO_REVEAL_COLLECT, own_key_only=True),
    Step("forager", "forager_person", (("linkedin_url",),),
         lambda i: {"op": "phone_numbers",
                    "linkedin_public_identifier": _slug(i["linkedin_url"])},
         _forager_phone, "Forager credits on a hit only"),
    Step("lemlist", "lemlist_enrich", (("linkedin_url",), _NAMES_AND_DOMAIN),
         lambda i: _lemlist_args(i, find_phone=True),
         _lemlist_phone, "lemlist credits per phone found (own key only; asynchronous)",
         collect=_LEMLIST_COLLECT),
    Step("dropcontact", "dropcontact_enrich", _DROPCONTACT_IDS,
         _dropcontact_args, _dropcontact_phone,
         "Dropcontact credits (own key only; asynchronous)",
         collect=_DROPCONTACT_COLLECT),
    Step("fullenrich", "fullenrich_enrich_linkedin", _FULLENRICH_IDS,
         lambda i: _fullenrich_args(i, "contact.phones"), _fullenrich_phone,
         "10 FullEnrich credits per phone found (asynchronous, ~30 s to 4 min)",
         collect=_FULLENRICH_COLLECT),
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
    # Billed PER PROFILE returned: a domain names one company (1); a name needs a few to
    # step over namesakes (3) — never more than the step can use.
    return {"op": "companies", "account": account,
            "size": 1 if inputs.get("domain") else 3}


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


# The LinkedIn company page — two Apify actors, each one bounded run (`timeout_secs`
# on Apify's side, a cost ceiling): learned on a real list (a trade-show exhibitor
# list, 2026-10), where they recovered the pages the data vendors did not have.
_APIFY_TIMEOUT_S = 25
_APIFY_MAX_USD = 0.05
#: The employees of a page, ~$0.003-0.008 a profile: `limit` (≤ 25) of them stays under it.
_APIFY_EMPLOYEES_MAX_USD = 0.10


def _apify_items(res) -> list:
    """`apify_run_sync` answers the item LIST, or `{error, run_id, status, items}` for a
    run that ended TIMED-OUT/FAILED — whose partial items are still worth reading."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and isinstance(res.get("items"), list):
        return res["items"]
    return []


_COMPANY_PAGE = re.compile(r"linkedin\.com/(?:company|school|showcase)/[^/?#\s]+", re.I)


def _company_page(url) -> Optional[str]:
    m = _COMPANY_PAGE.search(str(url or ""))
    return f"https://www.{m.group(0).lower()}/" if m else None


def _site_linkedin(res, inputs):
    """The LinkedIn links the company's OWN website carries (`linkedIns`). A company
    page wins; a big group's site often links the PARENT's page — the scrape cannot
    tell, so the result says where it came from (`linkedin_source`)."""
    for item in _apify_items(res):
        if not isinstance(item, dict):
            continue
        links = item.get("linkedIns") or item.get("linkedins") or []
        pages = [p for p in (_company_page(u) for u in links) if p]
        if pages:
            return {"linkedin_url": pages[0], "linkedin_source": "website"}
    return None


def _search_linkedin(res, inputs):
    """A LinkedIn company search by NAME returns its top hit — usually a namesake on a
    short name (a restaurant, a dev shop elsewhere): kept only when its website is the
    company's domain, or, without a domain, when its name is the name asked."""
    for item in _apify_items(res)[:3]:
        if not isinstance(item, dict):
            continue
        url = _company_page(_pick(item, "linkedinUrl", "url", "companyUrl", "link"))
        if not url:
            continue
        website = _domain(_pick(item, "website", "websiteUrl", "companyWebsite"))
        name = _pick(item, "name", "companyName")
        if inputs.get("domain"):
            if website != inputs["domain"]:
                continue
        elif not _same_company(inputs, name, None):
            continue
        return _clean({"linkedin_url": url, "linkedin_source": "search", "name": name,
                       "domain": website,
                       "employees": _pick(item, "employeeCount", "staffCount"),
                       "industry": _pick(item, "industry", "industries[0]")},
                      ("linkedin_url",))
    return None


def _one_row(key: str, label: str, cost: str, *variants: Step, fills: str = "") -> Step:
    """One row of the table made of several ways in (`Step.variants`): what a screen
    shows and a setting names once."""
    groups: list[tuple[str, ...]] = []
    for v in variants:
        groups += [g for g in v.requires if g not in groups]
    first = variants[0]
    return Step(key, first.tool, tuple(groups), first.build, first.extract, cost,
                free=all(v.free for v in variants), label=label, fills=fills,
                variants=variants)


COMPANY_STEPS: tuple[Step, ...] = (
    _one_row(
        "fr", "French registry", "free (French open data)",
        Step("fr_siren", "fr_get", (("siren",),),
             lambda i: {"siren": i["siren"]}, _fr_get_company, "free (French open data)",
             free=True),
        Step("fr_name", "fr_search", (("company_name", "fr"),),
             lambda i: {"query": i["company_name"], "per_page": 5},
             _fr_search_company, "free (French open data)",
             needs="company_name, and a French company (country omitted or FR)",
             free=True),
    ),
    Step("aiark", "linkedin_aiark_search", (("domain",), ("company_name",)),
         _aiark_company_args, _aiark_company, "AI Ark credits per company returned"),
    Step("apollo", "apollo_enrich_organization", (("domain",),),
         lambda i: {"domain": i["domain"]}, _apollo_company, "1 Apollo credit"),
    Step("theirstack", "theirstack_companies_search", (("domain",),),
         # Billed PER COMPANY returned (◎ 30 each): the domain names one — never ask 3.
         lambda i: {"extra": {"company_domain_or": [i["domain"]]}, "limit": 1},
         _theirstack_company, "~3 TheirStack credits per company"),
    Step("cognism", "cognism_enrich_account", (("domain",), ("company_name",)),
         lambda i: {k: v for k, v in {"name": i.get("company_name"),
                                      "domain": i.get("domain")}.items() if v},
         _cognism_company, "Cognism credits (per its contract)"),
    # Completion: the LinkedIn page, when no source above returned it — from the
    # company's own website first, then by a search on its name.
    _one_row(
        "linkedin", "LinkedIn page", "a few cents of Apify per try",
        Step("linkedin_site", "apify_run_sync", (("domain",),),
             lambda i: {"actor_id": "vdrmota/contact-info-scraper",
                        "run_input": {"startUrls": [{"url": f"https://www.{i['domain']}"}],
                                      "maxDepth": 1, "maxRequestsPerStartUrl": 4,
                                      "sameDomain": True},
                        "timeout_secs": _APIFY_TIMEOUT_S,
                        "max_total_charge_usd": _APIFY_MAX_USD},
             _site_linkedin, "a few cents of Apify per site"),
        Step("linkedin_search", "apify_run_sync", (("company_name",),),
             lambda i: {"actor_id": "harvestapi/linkedin-company",
                        "run_input": {"searches": [i["company_name"]]},
                        "max_items": 3, "timeout_secs": _APIFY_TIMEOUT_S,
                        "max_total_charge_usd": _APIFY_MAX_USD},
             _search_linkedin, "a few cents of Apify per search"),
        fills="linkedin_url",
    ),
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
         free=True, label="Your LinkedIn"),
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


# People at a company ------------------------------------------------------------------

#: The most people one call returns.
PEOPLE_MAX = 25


def _people_inputs(company_name, domain, company_linkedin_url, roles, country,
                   limit) -> dict:
    if isinstance(roles, str):
        roles = [r for r in re.split(r"[,;]", roles)]
    roles = [r.strip() for r in (roles or []) if r and r.strip()]
    page = _company_page(company_linkedin_url) if company_linkedin_url else None
    if company_linkedin_url and not page:
        raise _bad(f"`company_linkedin_url` must be a LinkedIn company page "
                   f"(linkedin.com/company/…) — got `{company_linkedin_url}`.")
    n = max(1, min(int(limit or 10), PEOPLE_MAX))
    return {"company_name": (company_name or "").strip() or None,
            "domain": _domain(domain), "company_linkedin_url": page,
            "roles": roles or None, "country": (country or "").strip() or None,
            "limit": n}


def _matches_roles(title, roles) -> bool:
    if not roles:
        return True
    t = _norm_name(str(title or ""))
    return any(_norm_name(r) and f" {_norm_name(r)} " in f" {t} " for r in roles)


def _people_find(people: list[dict], inputs: dict) -> Optional[dict]:
    """The people that name a person and match the roles asked, deduplicated by
    LinkedIn URL (else name), at most `limit` — a find when at least one remains."""
    out, seen = [], set()
    for p in people:
        p = {k: v for k, v in p.items() if v not in (None, "", [], {})}
        if not p.get("full_name") or not _matches_roles(p.get("title"), inputs.get("roles")):
            continue
        key = (p.get("linkedin_url") or p["full_name"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= inputs["limit"]:
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
    """Google results on `linkedin.com/in`: a title reads « Name – Title – Company |
    LinkedIn ». Kept only when the result names the company (title or snippet)."""
    asked = _norm_name(inputs.get("company_name") or (inputs.get("domain") or "").split(".")[0])
    rows = []
    for o in _get(res, "organic") or []:
        if not isinstance(o, dict) or "linkedin.com/in/" not in str(o.get("link", "")):
            continue
        text = _norm_name(f"{o.get('title', '')} {o.get('snippet', '')}")
        if asked and f" {asked} " not in f" {text} ":
            continue
        m = _LINKEDIN_RESULT.match(str(o.get("title", "")))
        if not m:
            continue
        parts = [x.strip() for x in re.split(r"\s[-–—]\s", m.group("rest")) if x.strip()]
        rows.append({"full_name": m.group("name").strip(),
                     "title": parts[0] if parts else None,
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
             "per_page": min(25, i["limit"] * 2)}.items() if v},
         _apollo_people, "free search (names obfuscated; reveal per person: 1 Apollo credit)",
         # Masked last names only: a fallback, never the answer while a source that
         # names people fully is left to try.
         weak=lambda found: all(p.get("name_obfuscated") for p in found["people"])),
    # Then by price, full names first: google (◎ 1, names + LinkedIn URLs, low recall) →
    # aiark (◎ 1 per profile) → the page's LinkedIn employees (Apify, up to ◎ 30).
    Step("google", "serper_search", (("company_name",), ("domain",)),
         lambda i: {"query": f'"{i.get("company_name") or i["domain"].split(".")[0]}" '
                             f"({_role_query(i)})",
                    "site_filter": "linkedin.com/in", "num": 10, "country": None,
                    "language": None},
         _serper_people, "1 Serper search", label="Google — LinkedIn profiles"),    Step("aiark", "linkedin_aiark_search", (("domain",),),
         lambda i: {"op": "people",
                    "account": {"domain": {"any": {"include": [i["domain"]]}}},
                    # Billed PER PROFILE returned: ask what the call keeps, not 3×.
                    "size": i["limit"]},
         _aiark_people, "AI Ark credits per person returned"),
    Step("linkedin_employees", "apify_run_sync", (("company_linkedin_url",),),
         lambda i: {"actor_id": "harvestapi/linkedin-company-employees",
                    "run_input": {"companies": [i["company_linkedin_url"]],
                                  "maxItems": i["limit"]},
                    "max_items": i["limit"],
                    "timeout_secs": _APIFY_TIMEOUT_S,
                    "max_total_charge_usd": _APIFY_EMPLOYEES_MAX_USD},
         _employees, "Apify, ~$0.003-0.008 per profile",
         label="LinkedIn employees"),

)


# --- the plan, for a screen ----------------------------------------------------------

#: What a step costs in TULINA credits when it runs on Tulina's key, as data for a
#: screen: the tool Tulina BILLS for the step (the collect tool, for FullEnrich) and,
#: when that tool has several prices, which row of the published credit grid applies
#: (`pick`: the row's `kind` or short name); `reveal`: the tool that turns what a FREE
#: step found into something usable, billed per person; `max_units`: the most units one
#: call can bill (a company search by name returns up to 3 profiles); `max_usd`: the cost
#: ceiling of an Apify run (its grid rate is per $0.01 of run cost); `per_item`: billed per
#: result returned, up to the call's own `limit`. The figure lives in the grid, never
#: here. A step absent from this table is billed on its own tool, if at all; on the
#: caller's OWN key, Tulina bills nothing — what the provider takes is not shown.
PRICES: dict[tuple[str, str], dict] = {
    ("waterfall_email", "apollo"): {"tool": "apollo_match_person"},
    ("waterfall_email", "aiark"): {"tool": "linkedin_aiark_person"},
    ("waterfall_email", "forager"): {"tool": "forager_person", "pick": "email"},
    ("waterfall_email", "fullenrich"): {"tool": "fullenrich_result",
                                        "pick": "work_emails"},
    ("waterfall_phone", "forager"): {"tool": "forager_person", "pick": "phone"},
    ("waterfall_phone", "fullenrich"): {"tool": "fullenrich_result", "pick": "phones"},
    ("waterfall_company", "aiark"): {"tool": "linkedin_aiark_search", "max_units": 3},
    ("waterfall_company", "apollo"): {"tool": "apollo_enrich_organization"},
    ("waterfall_company", "theirstack"): {"tool": "theirstack_companies_search"},
    ("waterfall_company", "linkedin"): {"tool": "apify_run_sync", "max_usd": _APIFY_MAX_USD * 2},
    ("waterfall_person", "aiark"): {"tool": "linkedin_aiark_person"},
    ("waterfall_person", "apollo"): {"tool": "apollo_match_person"},
    ("waterfall_person", "forager"): {"tool": "forager_person",
                                      "pick": "profile or search"},
    # The search is free; revealing ONE person it found is the billed gesture — a screen
    # says that price, or the free search reads as the whole cost.
    ("waterfall_people", "apollo"): {"tool": "apollo_search_people",
                                     "reveal": "apollo_match_person"},
    ("waterfall_people", "aiark"): {"tool": "linkedin_aiark_search", "per_item": True},
    ("waterfall_people", "linkedin_employees"): {"tool": "apify_run_sync",
                                                 "max_usd": _APIFY_EMPLOYEES_MAX_USD},
    ("waterfall_people", "google"): {"tool": "serper_search"},
}


#: Each waterfall connector's step table — what the plan below and the tools read.
STEPS_BY_NAMESPACE: dict[str, tuple[Step, ...]] = {
    "waterfall_email": EMAIL_STEPS, "waterfall_phone": PHONE_STEPS,
    "waterfall_company": COMPANY_STEPS, "waterfall_person": PERSON_STEPS,
    "waterfall_people": PEOPLE_STEPS,
}


def _step_row(step: Step, mode: Optional[str] = None, ns: Optional[str] = None) -> dict:
    from .. import providers
    from ..tool_visibility import namespace_of
    con = providers.connector_for_namespace(namespace_of(step.tool))
    row = {"key": step.key, "tool": step.tool,
           # The CONNECTOR the step goes through — what a screen shows (its logo, its
           # card), not the vault carrier (`fr_get` → `sirene`, aiark → `aiark`).
           "connector": con.name if con is not None else None,
           "label": step.label or (con.label if con is not None else step.key),
           "needs": _missing(step),
           # The same thing, as data: alternative groups of input keys (`fr` = a French
           # company) — what a screen translates, where `needs` is a sentence for agents.
           "requires": [list(g) for g in step.requires],
           "cost": step.cost, "free": step.free,
           # A completion step: runs after the cascade, only to fill this missing field.
           "fills": step.fills or None,
           "price": ({"rate": PRICES.get((ns, step.key)) or {"tool": step.tool}}
                     if ns and not step.free else None),
           "asynchronous": step.collect is not None}
    if mode is not None:
        row["key_mode"] = mode
        row["usable"] = mode not in _NO_KEY and not (
            step.own_key_only and mode not in _OWN_KEY and mode != "unknown")
    if step.own_key_only:
        row["own_key_only"] = True
    return row


def plan(ns: str, sub: Optional[str]) -> dict:
    """The waterfall's order for `sub` — what a call would try, spending nothing: where
    the order comes from (`setting` / `default`), each step with the key it would use,
    and every step the waterfall offers (`available`, the table's order), so a screen
    can show the cascade and let someone remove, reorder or add back a step. SYNC (it
    reads the vault and the cascade): off the loop."""
    steps = STEPS_BY_NAMESPACE[ns]
    order, ignored, source, modes = _plan(ns, steps, None, sub)
    return {"connector": ns, "order_from": source, "ignored_steps": ignored,
            "steps": [_step_row(s, modes.get(s.tool, "unknown"), ns) for s in order],
            "available": [_step_row(s, ns=ns) for s in steps]}


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
        domain / company) → aiark (LinkedIn URL) → kaspr (LinkedIn URL) → lusha (LinkedIn
        URL, or name + domain / company) → then the asynchronous ones: dropcontact
        (LinkedIn URL, or name + company / domain) → lemlist (LinkedIn URL, or name +
        domain) → fullenrich (LinkedIn URL, or name + domain — usually 30 s to 4 min, so
        often `pending`) → forager last (LinkedIn URL — on Tulina's key the dearest email
        lookup). A slow step is billed even when its answer comes back `pending`. The
        email is NOT verified here.

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

        Steps (default order): kaspr (LinkedIn URL) → lusha (LinkedIn URL, or name +
        domain / company) → apollo (phone reveal, your own Apollo key — asynchronous;
        LinkedIn URL, or name + domain / company) → forager (LinkedIn URL — on Tulina's
        key a phone lookup is dear) → then the asynchronous ones: lemlist (LinkedIn URL,
        or name + domain) → dropcontact (LinkedIn URL, or name + company / domain) →
        fullenrich (LinkedIn URL, or name + domain — usually 30 s to 4 min, so often
        `pending`). A slow step is billed even when its answer comes back `pending`. The
        LinkedIn URL opens the most steps. Phone credits cost more than email credits.

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

        Steps (default order): fr (the French registry, free — by SIREN, else by name for a
        French company) → aiark (domain or name) → apollo (domain) →
        theirstack (domain) → cognism (domain or name); then, when none of them gave
        the company's LinkedIn page: linkedin (the LinkedIn links on its own website,
        else a LinkedIn company search by name, kept only if its website is the
        domain). A search result counts only if
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
            country: ISO country code; other than FR skips the registry's name search.
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

    @mcp.tool(annotations=LECTURE)
    @documente(f"""Find the PEOPLE working at ONE company — its decision-makers when you
        give roles — trying sources in turn and stopping at the FIRST that returns at
        least one matching person (native waterfall).

        Steps (default order): apollo (domain — a free search; last names come back
        MASKED, reveal one by its `apollo_id` with `apollo_match_person`, a paid match:
        masked names alone do NOT stop the cascade, they are its fallback) → google (name
        or domain — LinkedIn profiles found on Google, full names) → aiark (domain) →
        linkedin_employees (the company's LinkedIn page — current titles straight from
        LinkedIn, often fresher than the data vendors; get the page with
        `waterfall_company`). A person is kept only when their title matches one of `roles` (when
        given), and, for LinkedIn and Google, only when their CURRENT company is the
        one asked for: a company page or a search pulls in people from elsewhere.

        {_ORDER_DOC}

        Returns `{{found, provider, people: {{people: [{{full_name, title?,
        linkedin_url?, location?, apollo_id?}}], count}}, attempts, order_from}}` —
        {_RECEIPT_DOC}

        Args:
            domain: the company's domain (`acme.com`; a URL is accepted).
            company_name: the company's name.
            company_linkedin_url: the company's LinkedIn page (linkedin.com/company/…).
            roles: role keywords to keep, e.g. ["sales", "CEO", "head of marketing"]
                (or one comma-separated string). Omitted = everyone the step returns.
            country: where the people are (e.g. "France") — the filter for a local
                subsidiary of a global domain (Apollo).
            limit: how many people at most (1-25, default 10).
{_COMMON_ARGS}
        """)
    async def waterfall_people(
        domain: Optional[str] = None, company_name: Optional[str] = None,
        company_linkedin_url: Optional[str] = None,
        roles: Optional[list[str]] = None, country: Optional[str] = None,
        limit: int = 10,
        providers: Optional[list[str]] = None, dry_run: bool = False,
    ) -> dict:
        inputs = _people_inputs(company_name, domain, company_linkedin_url, roles,
                                country, limit)
        return await _run_cascade(mcp, "waterfall_people", "people", PEOPLE_STEPS,
                                  inputs, providers, dry_run)
