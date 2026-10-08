"""What the Microsoft 365 SERVICE tools share — SharePoint & OneDrive, Outlook, Outlook
Calendar (and the next ones): the caller's token for ONE service, Graph's 4xx turned
into a named refusal, the argument checks, the markdown body.

A helper, not a connector: no `register()` (`tests/test_capabilities_drift.py`). Each
service module keeps its own `_client() -> <Client>` — the version-skew probe
(`tests/test_tools_client_methods_exist.py`) reads the concrete class there.

The token: `auth/microsoft.access_token_for(sub, service)` resolves the account the call
designates (`_account=`, the project's pin, the only one, the default) and refuses an
account that has not authorized THIS service, naming its card — before any Graph call.
"""
from __future__ import annotations

from typing import Callable, Optional, TypeVar

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


def token(service: str) -> str:
    """The caller's access token for `service`, on the account the call designates."""
    from oto.tools.microsoft import MicrosoftAuthError

    from ..auth import microsoft as ms_auth

    sub = access.current_user_sub_or_raise()
    try:
        return ms_auth.access_token_for(sub, service)
    except (RuntimeError, MicrosoftAuthError) as e:
        raise bad(str(e))


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


def run(fn: Callable[[], T], label: str, conflict: Optional[str] = None) -> T:
    """Graph 4xx → named refusal; a `ValueError` of the lib (an argument it refuses
    before any write) → its message. 429 and 5xx stay what they are: the error
    taxonomy classifies them as retryable."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except UpstreamHTTPError as e:
        if 400 <= e.status_code < 500 and e.status_code != 429:
            raise bad(upstream_message(e, label, conflict))
        raise
    except ValueError as e:
        raise bad(str(e))


def markdown_html(text: str) -> str:
    """A body written in markdown, as HTML — the very renderer of `gmail_compose`
    (tables, fenced code, lists), so that an agent writes the same way everywhere."""
    from oto.tools.google.gmail.lib.gmail_client import _markdown_to_html_fragment

    return _markdown_to_html_fragment(text)
