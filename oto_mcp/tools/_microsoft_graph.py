"""What the Microsoft 365 SERVICE tools share — SharePoint & OneDrive, Outlook, Outlook
Calendar, Teams, OneNote, the directory: the caller's token for ONE service, Graph's 4xx turned
into a named refusal, the argument checks, the markdown body.

A helper, not a connector: no `register()` (`tests/test_capabilities_drift.py`). Each
service module keeps its own `_client() -> <Client>` — the version-skew probe
(`tests/test_tools_client_methods_exist.py`) reads the concrete class there.

The token: `auth/microsoft.access_token_for(sub, service)` resolves the account the call
designates (`_account=`, the project's pin, the only one, the default) and refuses an
account that has not authorized THIS service, naming its card — before any Graph call.
The transport: every client goes out through the guarded session of
`auth/microsoft.graph_session` (`client_for`), the token travelling with each request.
"""
from __future__ import annotations

from typing import Callable, NoReturn, Optional, TypeVar

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError

T = TypeVar("T")


def bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def need(value, name: str, op: str):
    """Required argument for THIS op — an empty value counts as absent (an empty id
    list would pass for a success while nothing was asked)."""
    if value is None or (isinstance(value, (str, list)) and not value) or (
            isinstance(value, str) and not value.strip()):
        raise bad(f"op='{op}' requires `{name}`.")
    return value


def refuse_ignored(op: str, **provided) -> None:
    """An argument provided that THIS op does not use is an intent error."""
    for name, value in provided.items():
        if value is not None:
            raise bad(f"op='{op}' does not use `{name}`.")


def raw(objet: dict) -> dict:
    """`full=True`: the Graph object as upstream delivers it."""
    return objet


def token(service: str, *, renew: bool = False, account: Optional[str] = None) -> str:
    """The caller's access token for `service`, on the account the call designates.
    `renew=True` asks Entra for a new one whatever the cache holds (a permission granted
    since the cached token was issued — `access_token_for`). `account` names the account
    of a FILE SOURCE inside the call (an Outlook attachment dropped elsewhere,
    `file_source`); omitted, it is the call's own (`_account=`, pin, default)."""
    from oto.tools.microsoft import MicrosoftAuthError

    from ..auth import microsoft as ms_auth

    sub = access.current_user_sub_or_raise()
    args = (sub, service) + ((account,) if account else ())
    try:
        if renew:
            return ms_auth.access_token_for(*args, renew=True)
        return ms_auth.access_token_for(*args)
    except (RuntimeError, MicrosoftAuthError) as e:
        raise bad(str(e))


def client_for(cls: Callable[..., T], service: str, *, renew: bool = False,
               account: Optional[str] = None, **kwargs) -> T:
    """`cls(token, session=…, **kwargs)`: the oto-core client of `service` for the
    caller (`token`, same `renew` and `account`), every request through the guarded
    session (`auth/microsoft.graph_session`). The way a Microsoft client is built here."""
    from ..auth import microsoft as ms_auth

    return cls(token(service, renew=renew, account=account),
               session=ms_auth.graph_session(service), **kwargs)


#: The largest attachment Exchange Online carries (150 MB): the bound of a read whose
#: caller has no ceiling of its own (`outlook(op="attachment")`). No attachment Graph
#: can serve is refused by it; it only keeps the read finite.
EXCHANGE_ATTACHMENT_MAX = 150 * 1024 * 1024


def attachment(client, message_id: str, *, attachment_id: Optional[str] = None,
               filename: Optional[str] = None, max_bytes: int,
               label: str = "Outlook") -> tuple[dict, bytes]:
    """`({id, name, contentType}, bytes)` of a FILE attachment of `message_id`, designated
    by `attachment_id` OR `filename` — the single reader of `outlook(op="attachment")`
    and of the `outlook` file source (`file_source`), so both refuse alike.

    Read by `MailClient.get_attachment_content` (oto-core v1.255.0): the size Graph
    declares is judged against `max_bytes` BEFORE the content is asked for, then the
    content comes as a stream cut at `max_bytes` — never base64 in a JSON body. A
    designation by `filename` lists the attachments first, an `attachment_id` does not
    (an unknown one is named, with the list, once Graph says 404).
    Refusals are `McpError`s that name the gesture: ambiguous or unknown name, unknown
    id, an attached email or link (no bytes), a file over the limit."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.microsoft import AttachmentTooLarge, UnsupportedAttachment

    if (attachment_id is None) == (filename is None):
        raise bad("an Outlook attachment is designated by `attachment_id` OR `filename` "
                  "(only one) — both come from outlook(op='get').")
    pieces = None
    aid = attachment_id
    if aid is None:
        pieces = run(lambda: client.list_attachments(message_id), label)
        memes = [a for a in pieces if a.get("name") == filename]
        if len(memes) != 1:
            noms = [(a.get("name"), a.get("id")) for a in pieces]
            raise bad(f"{len(memes)} attachments are named {filename!r} on this "
                      f"message — pass `attachment_id` (name, id): {noms}.")
        aid = memes[0]["id"]
    try:
        contenu = client.get_attachment_content(message_id, aid, max_bytes=max_bytes)
    except AttachmentTooLarge as e:
        poids = (f"weighs {e.size} bytes, over" if e.size is not None
                 else "goes beyond")
        raise bad(f"« {e.name or filename or aid} » {poids} the {e.max_bytes} bytes "
                  "this gesture carries. Nothing was done.") from None
    except UnsupportedAttachment as e:
        raise bad(f"« {filename or aid} » is not a file attachment ({e.kind}): an "
                  "attached email or a link has no content to read here — open the "
                  "message in Outlook (`webLink` of op='get').") from None
    except UpstreamHTTPError as e:
        if e.status_code == 404 and pieces is None:
            pieces = run(lambda: client.list_attachments(message_id), label)
            if not any(a.get("id") == aid for a in pieces):
                noms = [(a.get("name"), a.get("id")) for a in pieces]
                raise bad(f"no attachment with id {aid!r} on this message — its "
                          f"attachments (name, id): {noms}.") from None
        raise_refusal(e, label)
    except ValueError as e:
        raise bad(str(e)) from None
    return {"id": aid, "name": contenu.name, "contentType": contenu.content_type}, \
        contenu.content


def upstream_message(e, label: str, conflict: Optional[str] = None) -> str:
    """Graph's refusal, said for the agent: what it means, and the gesture."""
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    detail = str((body.get("error") or {}).get("message") or e.body or "")[:400]
    if status == 401:
        return (f"Microsoft Graph rejects the token (HTTP 401): reconnect from "
                f"your connectors, \"{label}\". {detail}").strip()
    if status == 403:
        return (f"Microsoft Graph denies access (HTTP 403): this Microsoft account does "
                f"not have rights on this item, or its organization blocks oto. "
                f"{detail}").strip()
    if status == 404:
        return f"Microsoft Graph: not found (HTTP 404). {detail}".strip()
    if status == 409 and conflict:
        return f"Microsoft Graph: {conflict} (HTTP 409). {detail}".strip()
    return f"Microsoft Graph rejected the request (HTTP {status}): {detail}"


def raise_refusal(e, label: str, conflict: Optional[str] = None) -> NoReturn:
    """Raises what a Graph error becomes for the agent: a 4xx → named refusal; 429 and
    5xx stay what they are — the error taxonomy classifies them as retryable."""
    if 400 <= e.status_code < 500 and e.status_code != 429:
        raise bad(upstream_message(e, label, conflict)) from e
    raise e


def run(fn: Callable[[], T], label: str, conflict: Optional[str] = None) -> T:
    """`fn()`, a Graph error turned into its `refusal`; a `ValueError` of the lib (an
    argument it refuses before any write) → its message."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except UpstreamHTTPError as e:
        raise_refusal(e, label, conflict)
    except ValueError as e:
        raise bad(str(e))


def markdown_html(text: str, *, cid: bool = False) -> str:
    """A body written in markdown, as HTML — the very rendering of `gmail` (op=draft)
    and `email_send` (`markdown_assaini.rendu_markdown_assaini`: tables, fenced code,
    lists, `http(s)` images), so that an agent writes the same way everywhere. `cid=True`
    also keeps a `cid:` image — a message's inline attachment (`outlook`)."""
    from ..markdown_assaini import rendu_markdown_assaini

    return rendu_markdown_assaini(text, images=True, cid=cid)
