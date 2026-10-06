"""PayFit — the client, argument guards, upstream-error translation, file rendering
and the probe, shared by all of the connector's tool modules.

Split out of the tool modules to keep them under 500 lines. Five rules live here:

- **no argument is silently dropped**: "provided" reads as `is not None`,
  never truthiness — `fr=False` and `limit=0` are provided values;
- **an upstream error is classified on `status_code`**, never on the message text;
- **an empty key is refused BEFORE the client**: passed empty, `PayfitClient`
  would resolve `PAYFIT_API_KEY` from the SERVER's environment and work on a
  different company than the one whose key is set;
- **no write is wired**: every write op returns the named refusal
  `payfit_write_not_wired`, without resolving the key or calling PayFit (24/09/2026);
- **a document only goes out if the org's policy masks nothing**: a PDF or a
  file cannot be filtered, so it stays locked until the PayFit masks are
  lifted (`serve_document`). A verbatim EXCERPT of a document (the raw line of a
  payslip) follows the same lock (`documents_unlocked`).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import ecriture_non_cablee

if TYPE_CHECKING:
    from oto.tools.payfit import PayfitClient

_NAME = "payfit"
logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 50


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _client() -> PayfitClient:
    """The client for THIS caller's key. The real import is in the body: tests
    replace the package's class."""
    from oto.tools.payfit import PayfitClient

    key, _ = access.resolve_api_key(_NAME)
    if not isinstance(key, str) or not key.strip():
        # Empty, the client would look for a key in the server's environment.
        raise _bad("PayFit: no API key set for this connector.")
    return PayfitClient(api_key=key.strip())


def run(fn: Callable[[], Any]) -> Any:
    """Runs a client call; a validation or upstream error becomes an
    `INVALID_PARAMS` instruction."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise _bad(upstream_message(e)) from None


def need(op: str, **required: Any) -> None:
    missing = [n for n, v in required.items() if v is None or v == ""]
    if missing:
        raise _bad(f"op={op!r} requires {', '.join('`' + m + '`' for m in missing)}.")


def refuse_ignored(op: str, **provided: Any) -> None:
    """An argument provided that THIS op does not use is an intent error.
    `is not None`, never truthiness: `False` and `0` are provided values."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}`.")


def limit_or_default(limit: Optional[int]) -> int:
    return DEFAULT_LIMIT if limit is None else limit


def refuse_unknown_op(op: str, *allowed: str) -> McpError:
    return _bad(f"op={op!r} unknown — expected {', '.join(repr(a) for a in allowed)}.")


# ---------------------------------------------------------------------------
# Writes: NOT WIRED, ever (decision of 24/09/2026)
# ---------------------------------------------------------------------------

def not_wired(op: str, action: str, **what: Any) -> McpError:
    """The refusal of ANY PayFit write: nothing is sent, whatever the argument.
    The ability to write does not exist in the connector until it is decided —
    no org switch, no activation by an org_admin. Neither the key nor the
    client is touched: the refusal depends on nothing. Common shape for read-only
    connectors: `ecriture_non_cablee.refus`.

    `what` describes the action to the agent. The caller only puts identifiers,
    dates and labels in it — never a value masked by default (NIR, IBAN, absence
    reason), which would end up in the call log."""
    return ecriture_non_cablee.refus(_NAME, "PayFit", op, action, **what)


# ---------------------------------------------------------------------------
# The redaction notice: what the EFFECTIVE policy masks, not what it would
# mask by default
# ---------------------------------------------------------------------------

_SONDE = "sonde-de-redaction"


def _masque(ff, champ: str) -> bool:
    """True if policy `ff` rewrites or removes `champ` — tested through its
    `apply`, the very path of the output, rather than by re-reading its rules."""
    return ff.apply({champ: _SONDE}).get(champ) != _SONDE


def redaction_notice() -> str:
    """The notice served with every response that may carry a sensitive field.

    It says what the caller's EFFECTIVE policy masks — the same cascade
    as the output (`access.resolve_field_filter`: the active org's policy, else
    the server default). A constant notice used to announce "NIR, IBAN/BIC masked"
    even when the org had lifted the rule (oto signal #1269): the agent believed it
    was serving protected data, and told the user so.

    The sensitive fields are those of the server default (`SERVER_DEFAULTS["payfit"]`),
    named as they come out: those are the names the agent reads them under. An
    unreadable policy RAISES: the output is withheld anyway (`redaction.redact_payload`)."""
    from .. import field_filter_defaults

    ff = access.resolve_field_filter(_NAME)
    sensibles = [c for regle in field_filter_defaults.SERVER_DEFAULTS[_NAME]["rules"]
                 for c in regle["fields"]]
    masques = [c for c in sensibles if _masque(ff, c)]
    clairs = [c for c in sensibles if c not in masques]
    lever = ("an org_admin sets this connector's policy (dashboard, or "
             "`oto_org_settings domain=field_filters service=payfit`)")
    if not clairs:
        return (f"Effective redaction for PayFit (server redaction default, or org "
                f"policy): {', '.join(masques)} are masked. This is not missing "
                f"data — {lever}. `absence_category` stays readable in all "
                f"cases.")
    reste = (f"Still masked: {', '.join(masques)}." if masques
             else "No sensitive field is masked.")
    return (f"Effective redaction for PayFit: the org's policy leaves IN CLEAR "
            f"{', '.join(clairs)} — served as PayFit returns them. {reste} To "
            f"change this, {lever}.")


# ---------------------------------------------------------------------------
# Documents: PDF payslip, accounting export, payment file, document
# ---------------------------------------------------------------------------

# Refusals served to the agent: they say WHY and WHO can open — never a
# workaround. An agent that is suggested another route takes it.
DOCUMENTS_LOCKED = (
    "PayFit: document not served. It contains per-employee data (NIR, IBAN, "
    "names and amounts) that your org's field-filter policy masks for "
    "PayFit — and a filter cannot mask the inside of a file. To open "
    "PayFit documents, an org_admin of the org must lift the masks of the "
    "`payfit` connector (policy with no rule at all).")
DOCUMENTS_POLICY_UNREADABLE = (
    "PayFit: document not served. Your org's field-filter policy could "
    "not be read, and a document carrying NIR or IBAN does not go out without it. "
    "Retry in a moment; if it persists, it is an incident on oto's side.")
OVERTIME_LINE_LOCKED = (
    "PayFit: the raw payslip line (`line`) is not served. It is document text, "
    "which a field filter cannot see, and your org's field-filter policy "
    "masks PayFit fields. `kind`, `label`, `numbers` and "
    "`rates` are still served, filtered by this policy. To get the raw "
    "line, an org_admin of the org must lift the masks of the `payfit` connector "
    "(policy with no rule at all).")


def documents_open() -> bool:
    """The document LOCK: true only if the caller's EFFECTIVE policy for
    `payfit` masks NOTHING.

    The policy is read through the existing mechanism, `access.resolve_field_filter` —
    the same cascade as the JSON output (the active org's policy, else the server
    floor). Without an org policy, the floor applies: it masks NIR,
    IBAN, BIC and `absence_type`, so the lock is closed. It only opens on an
    EMPTY org policy (`rules: []`, authoritative).

    ⚠️ Why "masks nothing" and not "does not mask the NIR": a file cannot be
    filtered at all. An org that set ANY rule on `payfit` has said
    that a field must not go out; the PDF containing it would let it out anyway.
    The only state in which a document respects the policy is the one where it
    removes nothing."""
    return access.resolve_field_filter(_NAME).is_empty


def documents_unlocked() -> bool:
    """`documents_open`, **fail-closed**: an unreadable policy (database
    unavailable…) is a named refusal, never an open lock — exactly as
    `redaction.redact_payload` withholds a service's JSON output when it has a server default.
    To be read BEFORE any upstream call: what we will not serve is not downloaded."""
    try:
        return documents_open()
    except Exception as e:  # noqa: BLE001 — named refusal, fail-closed
        logger.warning("payfit: field-filter policy unreadable, document refused",
                       exc_info=True)
        raise _bad(DOCUMENTS_POLICY_UNREADABLE) from e


def serve_document(fetch: Callable[[], dict]) -> dict:
    """The ONLY path of a PayFit document to the agent: lock, THEN upstream call,
    THEN rendering. The document is not even downloaded when the lock is closed, nor
    when the policy is unreadable (`documents_unlocked`).

    Rendering goes through `file_content.render_for_agent`, the single home of the
    inline-vs-signed-URL rule."""
    from .. import file_content

    if not documents_unlocked():
        raise _bad(DOCUMENTS_LOCKED)
    blob = run(fetch)
    sub = access.current_user_sub_or_raise()
    try:
        return file_content.render_for_agent(
            blob.get("data") or b"", blob.get("filename") or "payfit",
            blob.get("mimetype") or "application/octet-stream",
            sub=sub, prefix="payfit-files")
    except file_content.MediaUnavailable as e:
        raise _bad(str(e)) from None


# ---------------------------------------------------------------------------
# Upstream errors & probe
# ---------------------------------------------------------------------------

def upstream_message(e: Any) -> str:
    status = e.status_code
    if status == 401:
        return "PayFit: API key rejected (HTTP 401) — invalid, revoked or inactive."
    if status == 403:
        return ("PayFit: access denied (HTTP 403) — the key does not carry the scope "
                "required by this resource (collaborators:read, contracts:read, "
                "time:read, contracts:payslips:read, accounting:read, "
                "payment-files:read, health-insurance:read/write, "
                "collaborators:meal-vouchers:read, or a write scope).")
    if status == 404:
        return "PayFit: resource not found (HTTP 404)."
    if status == 429:
        return "PayFit: too many requests (HTTP 429) — retry in a moment."
    if status >= 500:
        return f"PayFit is temporarily unavailable (HTTP {status})."
    return f"PayFit rejected the request (HTTP {status}): {e.body}"


def verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: key introspection then
    `GET /companies/{id}` (no scope required), with no side effect."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.payfit import PayfitClient

    key = (fields or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise connector_verify.NonAutorise("PayFit: empty API key.")
    try:
        PayfitClient(api_key=key.strip()).get_company()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(upstream_message(e)) from e
        raise


def register_probe() -> None:
    connector_verify.register(_NAME, verify)
