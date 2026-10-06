"""Meta Ads — a call's token, and the translation of refusals.

The other half of the connector: `auth/meta_ads.py` carries ACQUISITION; here we
serve. No renewal (BISU token with no expiry, see `auth/meta_ads.py`).

Four refusals, four gestures — and Meta returns all of them as a 400:
no connected account (authorize), dead authorization (reconnect — the vault row
is marked so that the card says so), rate limit reached (wait), outage
(retry).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import requests

from mcp.types import ErrorData, INVALID_PARAMS

from ..auth import meta_ads as ads_auth
from ..auth.meta_ads import CONNECTOR, _coeur, _ctx_org, _row, _scope
from ..connectors import health as connector_health
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation only — never evaluated at runtime
    from oto.tools.meta_ads import MetaAdsClient

logger = logging.getLogger("oto_mcp.tools.meta_ads")

_RECONNECTER = ("Reconnect it from your connectors page, connector « Meta Ads » — "
                "and check that the ad accounts are still granted.")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def resolve_token(sub: str) -> str:
    """The connected account's token, or a refusal that states the gesture."""
    org_id = _ctx_org(sub)
    row = _row(org_id, sub)
    if not row or not row.get("secret"):
        from .. import config
        raise RuntimeError(
            "No Meta Ads account connected. Authorize oto from your connectors page "
            f"({config.dashboard_url_for(sub)}/, connector « Meta Ads ») and pick "
            "the ad accounts to share.")
    return row["secret"]


def _marquer_mort(message: str) -> None:
    """Records the rejection on the caller's vault row: the card will say "needs
    reconnecting" without waiting for the next call. Never raises — the refusal
    itself goes out anyway. Synchronous: run on the execution thread (it reads the
    context and writes to the database)."""
    from .. import access

    try:
        sub = access.current_user_sub_or_raise()
        entity_type, entity_id = _scope(_ctx_org(sub), sub)
        connector_health.mark_rejected(entity_type, entity_id, CONNECTOR, "", message)
    except Exception:  # noqa: BLE001 — a failed marking does not mask the refusal
        logger.warning("meta_ads: health marking failed", exc_info=True)


def _jeton_de_l_appelant() -> str:
    """`resolve_token` for the current caller — all off the loop: resolving the sub
    and the org touches the database (`to_thread` copies the context)."""
    from .. import access

    return resolve_token(access.current_user_sub_or_raise())


async def _client() -> MetaAdsClient:
    """The Meta Ads client of THIS caller.

    The name and the UNQUOTED annotation are a contract: the version-skew probe
    (`tests/test_tools_client_methods_exist.py`) recognizes the `_client` factories
    to check, at the pinned oto-core tag, the methods the tools call.
    """
    try:
        jeton = await asyncio.to_thread(_jeton_de_l_appelant)
    except RuntimeError as e:
        raise _bad(str(e)) from e
    return _coeur().MetaAdsClient(jeton)


async def appeler(geste: str, fn, *args, **kwargs):
    """Runs a core call off the loop and translates its refusals.

    ⚠️ Only the text of errors WRITTEN by the core (`MetaAdsError`, with no URL or
    raw body) goes through; for the rest we return the TYPE — an exception from an
    intermediate layer may carry a URL or a header."""
    coeur = _coeur()
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except coeur.MetaAdsAuthExpired as e:
        message = f"Meta no longer accepts this authorization. {_RECONNECTER}"
        await asyncio.to_thread(_marquer_mort, message)
        raise _bad(message) from e
    except coeur.MetaAdsThrottled as e:
        raise _bad(str(e)) from e
    except (coeur.MetaAdsError, ValueError) as e:
        raise _bad(f"Meta could not serve {geste}: {e}") from e
    except requests.RequestException as e:
        # Network only (timeout, connection, truncated response): retryable. The
        # token goes in a header, never in the URL, so the `requests` message
        # does not carry it; we still only return the TYPE to the agent. Any other
        # type is a code bug: it bubbles up as is, traceback included.
        logger.warning("meta_ads: %s failed — %s", geste, type(e).__name__,
                       exc_info=True)
        raise _bad(
            f"Meta did not answer {geste} ({type(e).__name__}). This is not an "
            "authorization refusal — retry.") from e


def avertir_au_demarrage() -> None:
    """What the operator must know AT BOOT, in one line. Never raises."""
    ads_auth.avertir_au_demarrage()
    try:
        _coeur()
    except RuntimeError as e:
        logger.warning(
            "meta_ads: connector mounted but the core is not installed — the "
            "`meta_ads_*` tools will refuse, saying so. Detail: %s", e)
