"""Shared base of the `klaviyo` connector modules.

The connector spans two modules (`Connector.modules` in the registry):
`tools/klaviyo.py` (account, profiles, lists, segments, consent) and
`tools/klaviyo_marketing.py` (campaigns, flows, metrics, events, reports). This
file holds what they have in common — key resolution, translation of a Klaviyo
refusal, argument checks, the tightened views and the pagination block — so that
a fix never covers only half the connector. It has no `register()`: it is a
helper.

**Pagination, said honestly.** Klaviyo pages by cursor (`links.next`, a full URL).
Every list returns `next_cursor` (null on the last page) to pass back as
`page_cursor`; `all_pages=True` lets the client follow the pages itself, at most
`max_pages`, and the result then says whether it is `complete` — a walk stopped by
the bound keeps its `next_cursor`, it never passes for the whole set.

**Tightened views** (ADR 0047): a list returns the columns that identify and sort
its rows and NAMES what it left out (`omitted`); `full=True` returns Klaviyo's
JSON:API payload as is.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation of `_client()` only — never evaluated
    from oto.tools.klaviyo import KlaviyoClient

#: Where the user creates their key, in THEIR Klaviyo account.
WHERE_TO_CREATE = "Klaviyo → Settings → Account → API keys → \"Create Private API Key\""

#: Bound on `max_pages` (the client follows `links.next` itself).
MAX_PAGES = 20

#: Profile attributes Klaviyo accepts on a write (`/profile-import`, `/events`).
PROFILE_ATTRIBUTES = ("email", "phone_number", "external_id", "first_name",
                      "last_name", "organization", "title", "locale", "image",
                      "location", "properties")
PROFILE_IDENTIFIERS = ("email", "phone_number", "external_id")

#: What a profile row leaves out (returned by `op="get"` or `full=True`).
PROFILE_ROW_OMITTED = ("title", "locale", "image", "location", "properties")


def _client() -> KlaviyoClient:
    """The Klaviyo client for THIS caller's key (byo: a private key belongs to one
    Klaviyo account). Real import in the body: tests replace the client, and the
    version probe reads the return annotation."""
    from oto.tools.klaviyo import KlaviyoClient

    key, _is_platform = access.resolve_api_key("klaviyo")
    return KlaviyoClient(api_key=key)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """The "test connection" probe: `GET /accounts`, with no side effect. Covers the
    key and its `accounts:read` scope; a 401/403 raises `KlaviyoError` (an
    `UpstreamHTTPError`), which the probe's classification reads by its code."""
    from oto.tools.klaviyo import KlaviyoClient

    KlaviyoClient(api_key=fields["key"]).get_account()


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def hors_op(op: str, permis: Iterable[str], **fournis: Any) -> None:
    """An argument that THIS op does not use is an error of intent — otherwise
    `op="get"` with `filter=` would return one record while letting one believe
    the filter applied. `False` counts as not given (the default of a flag)."""
    permis = set(permis)
    for name, value in fournis.items():
        if value is not None and value is not False and name not in permis:
            raise _bad(f"op={op!r} does not use `{name}`.")


def need(op: str, **requis: Any) -> None:
    for name, value in requis.items():
        if value is None or value == [] or value == "":
            raise _bad(f"op={op!r} requires `{name}`.")


def _refusal(e: Any, scope: str) -> str:
    """A Klaviyo refusal (`KlaviyoError`) in an actionable sentence: its code, the
    library's message (which names the faulty part), and for 401/403 where to
    make a new key."""
    code = getattr(e, "code", None) or f"http_{e.status_code}"
    text = str(e.args[0]) if e.args else str(e)
    if e.status_code == 401:
        return f"{text} Create a private key in {WHERE_TO_CREATE}."
    if e.status_code == 403:
        return (f"{text} This call needs the scope {scope}: create a key that has "
                f"it in {WHERE_TO_CREATE}, then replace the one on the Klaviyo card.")
    return text if code in text else f"{text} ({code})"


def run(fn: Callable[[], Any], scope: str) -> Any:
    """Local check (ValueError) and 4xx → named refusal; 429 and 5xx stay what
    they are (`retryable`): retrying them later may succeed."""
    from oto.tools.common import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(f"Klaviyo: {e}") from None
    except UpstreamHTTPError as e:
        if 400 <= e.status_code < 500 and e.status_code != 429:
            raise _bad(_refusal(e, scope)) from None
        raise


def pages(all_pages: bool, max_pages: Optional[int]) -> Dict[str, Any]:
    """The walk arguments of the client, checked. A `page_cursor` given with
    `all_pages` is legitimate: the walk starts at that cursor."""
    if max_pages is not None and not all_pages:
        raise _bad("`max_pages` only applies with `all_pages=True`.")
    size = 10 if max_pages is None else max_pages
    if not 1 <= size <= MAX_PAGES:
        raise _bad(f"`max_pages` must be between 1 and {MAX_PAGES} (got {max_pages}).")
    return {"all_pages": all_pages, "max_pages": size}


def _attrs(item: Any) -> dict:
    return (item.get("attributes") or {}) if isinstance(item, dict) else {}


def rel_id(item: dict, name: str) -> Optional[str]:
    """The id of a to-one relationship (`relationships.<name>.data.id`)."""
    data = ((item.get("relationships") or {}).get(name) or {}).get("data")
    return data.get("id") if isinstance(data, dict) else None


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def listing(res: dict, key: str, slim: Callable[[dict], dict],
            omitted: Iterable[str] = (), *, all_pages: bool = False) -> dict:
    """A list page (or walk) in its tightened view, with the pagination block."""
    from oto.tools.klaviyo import next_cursor

    rows = [slim(i) for i in res.get("data") or [] if isinstance(i, dict)]
    out: Dict[str, Any] = {key: rows, "count": len(rows),
                           "next_cursor": next_cursor(res)}
    if all_pages:
        out["pages"] = res.get("pages")
        out["complete"] = out["next_cursor"] is None
    if omitted:
        out["omitted"] = list(omitted)
    return out


def included_names(res: dict) -> Dict[tuple, dict]:
    """`included` indexed by (type, id) — to name a related record in a row."""
    return {(i.get("type"), i.get("id")): i for i in res.get("included") or []
            if isinstance(i, dict)}


# ---------------------------------------------------------------------------
# Tightened views
# ---------------------------------------------------------------------------

def slim_profile(item: dict) -> dict:
    a = _attrs(item)
    out = {"id": item.get("id"), "email": a.get("email"),
           "phone_number": a.get("phone_number"), "external_id": a.get("external_id"),
           "first_name": a.get("first_name"), "last_name": a.get("last_name"),
           "organization": a.get("organization"), "created": a.get("created"),
           "updated": a.get("updated"), "last_event_date": a.get("last_event_date"),
           "subscriptions": a.get("subscriptions"),
           "predictive_analytics": a.get("predictive_analytics")}
    return _compact(out)


def profile_record(item: dict, included: Iterable[dict] = ()) -> dict:
    """One profile: every attribute (one record is not the budget problem), and the
    lists and segments asked by `include`, named."""
    out = {"id": item.get("id"), **{k: v for k, v in _attrs(item).items() if v is not None}}
    for kind, key in (("list", "lists"), ("segment", "segments")):
        rows = [{"id": i.get("id"), "name": _attrs(i).get("name")}
                for i in included if isinstance(i, dict) and i.get("type") == kind]
        if rows:
            out[key] = rows
    return out


def slim_group(item: dict) -> dict:
    """A list or a segment."""
    a = _attrs(item)
    return _compact({"id": item.get("id"), "name": a.get("name"),
                     "created": a.get("created"), "updated": a.get("updated"),
                     "opt_in_process": a.get("opt_in_process"),
                     "is_active": a.get("is_active"),
                     "is_processing": a.get("is_processing"),
                     "is_starred": a.get("is_starred"),
                     "profile_count": a.get("profile_count")})


def profile_refs(ids: Optional[List[str]], limit: int = 1000) -> List[dict]:
    """`profile_ids` → `[{type: "profile", id}]`, checked before anything is sent
    (a preview must refuse what the real call would refuse)."""
    if not isinstance(ids, list) or not 0 < len(ids) <= limit:
        raise _bad(f"`profile_ids` takes 1 to {limit} Klaviyo profile ids.")
    bad = [i for i in ids if not isinstance(i, str) or not i.isalnum() or not i.isascii()]
    if bad:
        raise _bad(f"`profile_ids`: {bad[:3]} are not Klaviyo profile ids (26 "
                   "letters and digits, 01H…) — an email is not an id: find the "
                   "profile with klaviyo_profiles(filter='equals(email,\"…\")').")
    return [{"type": "profile", "id": i} for i in ids]


def preview(action: str, effect: str, **details: Any) -> dict:
    """What a SENSITIVE write would do — nothing was sent to Klaviyo."""
    return {"preview": True, "sent": False, "action": action, "effect": effect,
            **_compact(details),
            "next": "nothing was sent — call again with the same arguments and "
                    "confirm=True to do it."}


def profiles_for(items: List[Any], channels: List[str], *, consent: str,
                 limit: int, consented_at: Optional[str] = None) -> List[dict]:
    """`profiles` (`[{email?, phone_number?}]`) → the JSON:API profiles of a consent
    job. Each requested channel needs its identifier on each profile: email
    consent without an email, or SMS consent without a phone number, is refused
    here rather than by an asynchronous job nobody watches."""
    if not isinstance(items, list) or not 0 < len(items) <= limit:
        raise _bad(f"`profiles` takes 1 to {limit} objects {{email?, phone_number?}} "
                   "per call.")
    marketing: Dict[str, Any] = {"consent": consent}
    if consented_at:
        marketing["consented_at"] = consented_at
    out = []
    for n, p in enumerate(items):
        if not isinstance(p, dict):
            raise _bad(f"profiles[{n}] must be an object {{email?, phone_number?}}.")
        extra = set(p) - {"email", "phone_number"}
        if extra:
            raise _bad(f"profiles[{n}]: unknown key(s) {sorted(extra)} — only email "
                       "and phone_number identify a profile for consent.")
        for ch, ident in (("email", "email"), ("sms", "phone_number")):
            if ch in channels and not p.get(ident):
                raise _bad(f"profiles[{n}]: {ch} consent needs `{ident}`.")
        attrs = {k: p[k] for k in ("email", "phone_number") if p.get(k)}
        attrs["subscriptions"] = {ch: {"marketing": dict(marketing)} for ch in channels}
        out.append({"type": "profile", "attributes": attrs})
    return out


def split_clauses(text: Optional[str]) -> List[str]:
    """A filter string → its clauses: split on the commas OUTSIDE parentheses,
    brackets and quotes (`equals(a,"x"),any(b,["p","q"])` → two clauses)."""
    if not text:
        return []
    clauses, depth, quote, cur = [], 0, None, []
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            clauses.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    clauses.append("".join(cur).strip())
    return [c for c in clauses if c]
