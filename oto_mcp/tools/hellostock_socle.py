"""Shared base of the `hellostock` connector modules (reads, writes).

The connector spans two modules (`Connector.modules` in the registry): the
reads in `tools/hellostock.py`, the three actions that act on the
production marketplace in `tools/hellostock_ecritures.py`. This file carries
what they have in common — token resolution, the translation of a HelloStock
refusal into guidance, the projection of a page — so that a fix never
covers half the connector. It has no `register()`: it is not a
connector, it is a helper.

**The two authentication refusals do not look alike, and are not cured the
same way.** 401: the token is unknown or revoked — a new one is created. 403: the token
is good but its account is not an administrator (the role is re-read on every call,
so a demoted account goes from 200 to 403 without the token changing) — recreating
a token will not help. Confusing them would send the user to the wrong action.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError
from .. import access, output_projection

if TYPE_CHECKING:  # the annotation of `_client()` only — never evaluated
    from oto.tools.hellostock import HelloStockAdminClient

# Where the user creates their token, in THEIR HelloStock account (a French-only UI: the
# original labels follow the English path so they can be found on the screen).
OU_CREER_LE_JETON = ("hellostock.fr → My account → Settings → API tokens "
                     "(in the French UI: Mon espace → Réglages → Jetons d'API)")


def _client() -> HelloStockAdminClient:
    """The HelloStock client for THIS caller's token (byo_user only: the token
    is personal, it carries the rights of its holder).

    The real import is done in the body: tests replace the client, and the
    version-skew probe reads the return annotation to check that the methods
    called by the two modules exist in the pinned oto-core.
    """
    from oto.tools.hellostock import HelloStockAdminClient

    key, _is_platform = access.resolve_api_key("hellostock")
    return HelloStockAdminClient(token=key)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _carte() -> str:
    """Where the token is REPLACED on the oto side, for THIS account (the address follows
    the caller's tenant, never a hard-coded address)."""
    from .. import config
    from ..auth.hooks import current_user_sub_from_token
    return f"{config.dashboard_url_for(current_user_sub_from_token())}/account"


def refus(e: Any, *, carte: bool = True) -> str:
    """The guidance matching a HelloStock refusal (`UpstreamHTTPError`)."""
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    detail = body.get("error") or (e.body if isinstance(e.body, str) else "")
    ou = f" — HelloStock card of your oto account: {_carte()}" if carte else ""
    if status == 401:
        return ("HelloStock refuses this token (401): it is unknown or revoked. Create a "
                f"new token at {OU_CREER_LE_JETON} (it is only shown once), "
                f"then replace the old one{ou}.")
    if status == 403:
        return ("HelloStock recognizes this token, but its account is not an administrator "
                "of the marketplace (403): this API is reserved for them, and recreating a "
                "token will not change that. That account must be given the "
                "administrator role in HelloStock, or the token of an account that "
                f"has it must be set{ou}.")
    if status == 404:
        return f"HelloStock: {detail or 'record not found'} (404) — check the identifier."
    return f"HelloStock refused the request (HTTP {status}): {detail or e.body}"


def traduire(e: Any) -> Exception:
    """The exception to raise for a HelloStock refusal (`UpstreamHTTPError`).

    4xx → named refusal (the call is to be changed, or the token). 429 and 5xx stay what
    they are: the error taxonomy classes them as retryable, rightly."""
    if 400 <= e.status_code < 500 and e.status_code != 429:
        return _bad(refus(e))
    return e


def _run(fn: Callable[[], Any]) -> Any:
    """Runs a client call and translates what comes back into guidance.

    A `HelloStockProtocolError` (redirect, non-JSON body) is NOT translated:
    it is a configuration defect on our side, which must be seen as it is.
    """
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise traduire(e) from None


def _hors_op(op: str, **donnes: Any) -> None:
    """Refuses an argument that does not apply to the chosen `op`, rather than
    ignoring it: a filter passed to `op="get"` would suggest it filtered."""
    en_trop = sorted(k for k, v in donnes.items() if v not in (None, False))
    if en_trop:
        raise _bad(f"op='{op}' does not take {en_trop}.")


def projeter(page: Any, drop: Iterable[str], full: bool) -> Any:
    """Page `{items, nextCursor, total}` → same keys, each item without the
    `drop` columns, and a `projection` block that NAMES what was removed.
    `full=True` returns the page as HelloStock served it. Never a cut in
    a text: whole columns are removed, and it is said so."""
    drop = tuple(drop)
    if full or not drop or not isinstance(page, dict):
        return page
    out = output_projection.project(page, items_path="items", item_drop=drop)
    out["projection"] = {"omitted": list(drop),
                         "hint": "full=True returns the whole records"}
    return out
