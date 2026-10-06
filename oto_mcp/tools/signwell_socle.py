"""Shared base of the `signwell` connector modules (documents and templates;
bulk sends, webhooks and account).

The connector spans two modules (`Connector.modules` in the registry):
`tools/signwell.py` (documents, templates) and `tools/signwell_envois.py` (bulk
sends, webhooks, account). This file holds what they have in common — key
resolution, translation of a SignWell refusal, the tightened view of a
document, argument validation — so that a fix never covers
only half the connector. It has no `register()`: it is a helper.

**What the document view fixes.** SignWell returns each recipient's signing link
under TWO keys depending on the mode: `signing_url` when it sends the invitation
itself, `embedded_signing_url` in embedded signing (the other is then null). And nothing in the payload says whether a recipient will
really receive an email: a document in `test_mode` diverts ALL
invitations to the account holder (verified live), an embedded document
only writes to recipients marked `send_email`. The view therefore returns a single link
(`signing_link`) and an explicit `emailed` per recipient.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError
from .. import access

if TYPE_CHECKING:  # only for the `_client()` annotation — never evaluated
    from oto.tools.signwell import SignWellClient

#: Where the user creates their key, in THEIR SignWell account.
OU_CREER_LA_CLE = "SignWell → Settings → API → \"Create API key\""


def _client() -> SignWellClient:
    """The SignWell client for THIS caller's key (byo: the key acts on behalf of the
    account that created it — that name is what appears on the invitations).

    The real import is done in the body: tests replace the client, and the
    version-skew probe reads the return annotation to check that the
    methods called exist in the pinned oto-core.
    """
    from oto.tools.signwell import SignWellClient

    key, _is_platform = access.resolve_api_key("signwell")
    return SignWellClient(api_key=key)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _detail(body: Any) -> str:
    """The readable reason for a SignWell refusal. Shape observed live:
    `{message, meta: {error, message, messages[]}}` — `meta.messages` carries the
    detail (field at fault), `message` the summary."""
    if not isinstance(body, dict):
        return str(body or "")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    messages = meta.get("messages") or ([meta["message"]] if meta.get("message") else [])
    if isinstance(messages, list) and messages:
        return " ; ".join(str(m) for m in messages)
    return str(body.get("message") or body.get("errors") or body)


def refus(e: Any) -> str:
    """The instruction matching a SignWell refusal (`UpstreamHTTPError`)."""
    status, detail = e.status_code, _detail(e.body)
    if status == 401:
        return ("SignWell rejects this key (401): it is unknown or has been deleted. "
                f"Create a key at {OU_CREER_LA_CLE}, then replace the one on your account's "
                "SignWell card.")
    if status == 404:
        return (f"SignWell: not found (404){' — ' + detail if detail else ''}. "
                "The API has NO document list: a document identifier cannot "
                "be found again, it must be kept from the response that created it.")
    if status == 409:
        return f"SignWell refuses the operation in the document's current state (409): {detail}"
    return f"SignWell refused the request (HTTP {status}): {detail}"


def traduire(e: Any) -> Exception:
    """4xx → named refusal (the call is to be changed, or the key). 429 and 5xx remain
    what they are: the error taxonomy classes them as retryable, rightly."""
    if 400 <= e.status_code < 500 and e.status_code != 429:
        return _bad(refus(e))
    return e


def _run(fn: Callable[[], Any]) -> Any:
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise traduire(e) from None


def _hors_op(op: str, **donnes: Any) -> None:
    """Refuse an argument that does not apply to the chosen `op`, rather than
    ignoring it: `signwell_document(op="get", subject=…)` would suggest the
    subject was set.

    `None` = not passed, and it is the ONLY "absent": an explicit `False` is a given
    argument, hence refused like the others (`full=False` on an `op` without a
    tightened view is not "nothing"). Hence the tools' booleans as `Optional[bool] =
    None` — a bare `bool = False` would make omission and `False` indistinguishable."""
    en_trop = sorted(k for k, v in donnes.items() if v is not None)
    if en_trop:
        raise _bad(f"op='{op}' does not take {en_trop}.")


def _need(op: str, **requis: Any) -> None:
    manquants = [k for k, v in requis.items() if v is None]
    if manquants:
        raise _bad(f"op='{op}' requires {', '.join('`' + m + '`' for m in manquants)}.")


def options_valides(op: str, options: Optional[Dict[str, Any]],
                    permises: Iterable[str]) -> Dict[str, Any]:
    """`options` carries the spec's rare settings. An unknown key is REFUSED
    by naming the allowed keys: SignWell would ignore a typo without saying
    anything, and the intended setting would never be applied."""
    options = dict(options or {})
    permises = tuple(permises)
    inconnues = sorted(set(options) - set(permises))
    if inconnues:
        raise _bad(f"op='{op}': unknown options {inconnues}. Allowed: {sorted(permises)}.")
    return {k: v for k, v in options.items() if v is not None}


def fichiers_valides(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Each file: `name` + exactly ONE source (`file_url` or `file_base64`)."""
    if not files:
        raise _bad("`files` is required: [{name, file_url | file_base64}].")
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not f.get("name"):
            raise _bad(f"files[{i}]: `name` required (with its extension, e.g. « nda.pdf »).")
        if bool(f.get("file_url")) == bool(f.get("file_base64")):
            raise _bad(f"files[{i}]: exactly ONE source, `file_url` OR `file_base64`.")
    return files


def destinataires_valides(recipients: Optional[List[Dict[str, Any]]], *,
                          email_requis: bool = True) -> List[Dict[str, Any]]:
    """A unique `id` per recipient (it is what `fields` and the text tags target),
    and an address — except for a recipient reached by SMS alone."""
    if not recipients:
        raise _bad("`recipients` is required: [{id, name, email}].")
    vus = set()
    for i, r in enumerate(recipients):
        if not isinstance(r, dict) or r.get("id") in (None, ""):
            raise _bad(f"recipients[{i}]: `id` required (« 1 », « 2 »… — it is the signer "
                       "number of the text tags).")
        rid = str(r["id"])
        if rid in vus:
            raise _bad(f"recipients: the id « {rid} » is repeated.")
        vus.add(rid)
        if email_requis and not r.get("email") and r.get("delivery_method") != "sms":
            raise _bad(f"recipients[{i}]: `email` required (or `delivery_method=\"sms\"`).")
    return recipients


def sans_base64(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """What can be echoed of a file: never its base64 content."""
    return [{"name": f.get("name"),
             "source": "file_url" if f.get("file_url") else "file_base64"}
            for f in (files or []) if isinstance(f, dict)]


def _emailed(doc: Dict[str, Any], r: Dict[str, Any]) -> Optional[bool]:
    """Does this recipient receive the invitation by email? `None` = not yet
    decidable (unsent draft)."""
    if doc.get("test_mode"):
        return False  # diverted to the account holder
    if doc.get("embedded_signing"):
        return bool(r.get("send_email"))
    if r.get("delivery_method") == "sms":
        return False
    if str(doc.get("status") or "").lower() == "draft":
        return None
    return True


def vue_document(doc: Any) -> Any:
    """The tightened view of a document: state, mode, and for each recipient ONE
    link and an explicit `emailed`. The fields, files and rules stay in the raw
    payload (`full=True`)."""
    if not isinstance(doc, dict):
        return doc
    recipients = []
    for r in doc.get("recipients") or []:
        if not isinstance(r, dict):
            continue
        recipients.append({
            "id": r.get("id"), "name": r.get("name"), "email": r.get("email"),
            "status": r.get("status"), "signing_order": r.get("signing_order"),
            "signing_link": r.get("embedded_signing_url") or r.get("signing_url"),
            "emailed": _emailed(doc, r),
            "bounced": r.get("bounced"),
        })
    fields = doc.get("fields") or []
    vue = {
        "id": doc.get("id"), "name": doc.get("name"), "status": doc.get("status"),
        "test_mode": doc.get("test_mode"), "embedded_signing": doc.get("embedded_signing"),
        "apply_signing_order": doc.get("apply_signing_order"),
        "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at"),
        "error_message": doc.get("error_message"),
        "fields_count": sum(len(p) if isinstance(p, list) else 1 for p in fields),
        "files": [f.get("name") for f in doc.get("files") or [] if isinstance(f, dict)],
        "recipients": recipients,
    }
    notes = []
    if doc.get("test_mode"):
        notes.append("test_mode: no invitation goes out to the recipients — SignWell "
                     "sends them to the account HOLDER, subject prefixed [TEST]. Document "
                     "with no legal value.")
    if str(doc.get("status") or "") in ("Created", "Sending"):
        notes.append(f"status « {doc.get('status')} »: SignWell is still processing the document "
                     "— fields coming from text tags arrive deferred, and nothing is "
                     "sent until the status is « Sent » (or « Draft » for a "
                     "draft). Re-read it in a few seconds.")
    if doc.get("embedded_signing"):
        notes.append("embedded signing: SignWell does not check the signer's "
                     "address — whoever holds `signing_link` can sign. Only "
                     "pass it to the intended person.")
    if notes:
        vue["notes"] = notes
    return vue
