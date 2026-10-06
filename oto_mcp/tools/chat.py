"""Google Chat — oto-core surface (ChatClient) exposed per user, multi-account.

List spaces (rooms + DMs), read messages, post (into a space or as a DM to a
user). **Restricted** scopes `chat.spaces.readonly` + `chat.messages`.
Default account or targeted by `account`. Per-user via OAuth.

**Consolidated surface (ADR 0047 §Amendment, applied to the Google Chat product)**: one
tool per business OBJECT, the verb as an `op` parameter — `chat_message` (list/send, the
message of a space or a DM). `chat_spaces` stays ALONE: it is the DISCOVERY that
PRODUCES the `space` the other consumes, its filter parameter (`space_type`) makes no
sense on a message op, and it takes no `space` — its params do not overlap with
its neighbor's (same case as `zoho_modules`). 3 tools → 2.

⚠️ `chat_message(op="send")` really POSTS into a real Google Chat space, under the
user's identity (not a bot). Hence: the default is `op="list"` (a READ), no
missing argument falls back to a send, and an ambiguous destination (`space` AND `user`,
or neither) is refused instead of guessed.
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..auth import google as google_oauth


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — actionable error, never a fallback."""
    if value is None:
        raise _bad(f"op='{op}' requires {name}")
    return value


def _http_error(e) -> McpError:
    """Normalizes a `googleapiclient.errors.HttpError` (raw unreadable stacktrace)
    into an actionable message, aligned with the messaging family (oto-backend#110).

    The 404 "Google Chat app not found" says nothing about the ACCOUNT: Google requires,
    for any write under the user's identity, that a Chat app be configured in the
    Google Cloud project of the OAuth client that issued the token (reads do without).
    It is the configuration of that client — ours, or that of a tenant that set up its
    own Google app —, not a user gesture: saying otherwise sends them to reconnect an
    account that has nothing to do with it (otomata-tech/oto#190)."""
    status = getattr(getattr(e, "resp", None), "status", None) or getattr(e, "status_code", None)
    detail = ""
    try:
        import json
        payload = json.loads(e.content.decode()) if getattr(e, "content", None) else {}
        detail = (payload.get("error") or {}).get("message") or ""
    # noqa: SILENT — non-JSON error body: the raw message is still returned
    except Exception:  # noqa: BLE001
        pass
    detail = detail or (getattr(e, "reason", None) or "").strip() or "unknown error"
    low = detail.lower()
    if status == 404 and ("app not found" in low or "chat api" in low or "turn on" in low):
        msg = ("Google Chat refuses to write: no Chat app is configured "
               "in the Google Cloud project of the OAuth client that issued this "
               "account's connection (Google requires it to write, not to read). It is a "
               "configuration of that OAuth client, to be done by its administrator in "
               "the Google Cloud console (Google Chat API → Configuration): reconnecting "
               f"the account or retrying changes nothing. Google detail: {detail}")
    else:
        msg = f"Google Chat refused the request (HTTP {status}): {detail}"
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


async def _call(fn, *args):
    """Runs a Chat client call off the loop + translates any `HttpError` into a clean
    error (never a raw stacktrace sent back to the agent)."""
    from googleapiclient.errors import HttpError
    try:
        return await asyncio.to_thread(fn, *args)
    except HttpError as e:
        raise _http_error(e)


def _client_for_user(account: Optional[str] = None):
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service="chat")
    except RuntimeError as e:
        raise _bad(str(e))
    from oto.tools.google.chat.lib.chat_client import ChatClient
    return ChatClient(credentials=creds)


_GOOGLE_CLIENT_TIMEOUT_S = 20
# oto-backend#867 lot 2 — see gmail.py::_client_for_user_async for the
# rationale (same token refresh mechanism, same method).
async def _client_for_user_async(account: Optional[str] = None):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_GOOGLE_CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _bad(f"Google did not respond within {_GOOGLE_CLIENT_TIMEOUT_S}s "
                   "(token refresh) — retry.")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def chat_spaces(
        space_type: Optional[str] = None, max_results: int = 100, account: Optional[str] = None,
    ) -> dict:
        """List the Google Chat spaces (rooms + DMs) the user belongs to.

        Returns {spaces: [{name, type, displayName, ...}], count}. Use a `name`
        ('spaces/XXXX') as the `space` argument of `chat_message`.

        Args:
            space_type: optional filter — "SPACE" (rooms) or "DIRECT_MESSAGE" (DMs).
            max_results: cap on spaces returned.
            account: email of the Google account to use (default if omitted).
        """
        client = await _client_for_user_async(account)
        filter_ = f'spaceType = "{space_type}"' if space_type else None
        spaces = await _call(client.list_spaces, filter_, max_results)
        return {"spaces": spaces, "count": len(spaces)}

    @mcp.tool()
    async def chat_message(
        op: Literal["list", "send"] = "list",
        space: Optional[str] = None,
        text: Optional[str] = None,
        user: Optional[str] = None,
        max_results: int = 20,
        account: Optional[str] = None,
    ) -> dict:
        """The messages of a Google Chat space — read them, or post one.

        `op`:
        - **"list"** (default): list recent messages in a space (most recent first).
          `space` = 'spaces/XXXX'.
        - **"send"**: post a Google Chat message — either into a space or as a DM to
          a user. Provide EITHER `space` OR `user`, not both.
          ⚠️ This WRITES: the message is really posted, under the user's own
          identity (not a bot), and cannot be unsent from here.

        Args:
            op: list (default) | send.
            space: target space resource name ('spaces/XXXX'), as returned by
                `chat_spaces` — required for op="list", and for op="send" into a
                room/space.
            text: op="send" — message text (basic formatting: *bold*, _italic_).
            user: op="send" — recipient email, sends a direct message (resolves the
                DM space). The DM space must ALREADY exist: Google Chat does not let
                a user create a brand-new DM space through the API — open the
                conversation once in Chat, then retry.
            max_results: op="list" — cap on messages returned.
            account: email of the Google account to use (default if omitted).
        """
        client = await _client_for_user_async(account)

        if op == "list":
            if space is None:
                raise _bad("op='list' requires space ('spaces/XXXX', see chat_spaces) "
                           "— `user` only applies to op='send'.")
            messages = await _call(client.list_messages, space, max_results)
            return {"messages": messages, "count": len(messages)}

        if op == "send":
            if bool(space) == bool(user):
                raise _bad("op='send' requires either `space` (message in a space) "
                           "or `user` (DM), not both and not neither.")
            _need(text, "text", op)
            if user:
                return await _call(client.send_dm, user, text)
            return await _call(client.send, space, text)

        raise _bad("op must be 'list' or 'send'")
