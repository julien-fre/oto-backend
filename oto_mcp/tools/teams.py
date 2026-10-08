"""Teams — a person's Microsoft Teams, via Microsoft Graph (`TeamsClient`).

Credential = the PERSON's Microsoft 365 account (carrier `microsoft`, OAuth, delegated
permissions) that has authorized THIS service (scopes `TEAMS`); several linked accounts
are chosen by the generic `_account=` axis.

**Surface**, one tool per object:
- `teams_spaces` (teams/channels/chats) — where one can read and post;
- `teams_message` (list/replies/post/reply) — the messages of a channel
  (`team_id` + `channel_id`) or of a chat (`chat_id`), exactly one of the two.

**Two consent levels** (`oto.tools.microsoft.teams`): the person consents alone to
teams, channels, posting and chats; READING a channel's messages (`list`/`replies` on a
channel) needs the administrator tier `scopes.TEAMS_ADMIN`, which only a tenant
administrator grants (`auth/microsoft.SERVICE_ADMIN_TIER`). Without it, the read is
refused with the approval link to send to that administrator (`admin_consent_url`) —
never a mute 403. An approval is only seen at a token renewal: when the vault does not
know it yet, or Graph still answers 403 while the vault says it is granted (a token
cached before the approval), the read renews the token ONCE, then decides.

⚠️ `post` and `reply` WRITE: a message is visible at once to the channel or chat
members; Teams has no draft. The default `op` of `teams_message` is `list` — a READ.
"""
from __future__ import annotations

import html
import re
from typing import Literal, Optional

from fastmcp import FastMCP

from .. import access
from ._microsoft_graph import (bad, markdown_html, need, raise_refusal, raw, refuse_ignored,
                               run, token)

#: The Microsoft service these tools call: its scopes, its card (`auth/microsoft`).
_SERVICE = "teams"
_LABEL = "Teams"
_BALISE = re.compile(r"<[^>]+>")


def _equipe(t: dict) -> dict:
    return {k: t.get(k) for k in ("id", "displayName", "description")}


def _canal(ch: dict) -> dict:
    return {k: ch.get(k) for k in ("id", "displayName", "description", "membershipType",
                                   "webUrl")}


def _conversation(ch: dict) -> dict:
    return {k: ch.get(k) for k in ("id", "topic", "chatType", "lastUpdatedDateTime",
                                   "webUrl")}


def _texte(corps: Optional[dict]) -> Optional[str]:
    """A message body as text: Teams sends HTML; the view keeps what a person reads."""
    contenu = (corps or {}).get("content")
    if contenu is None or str((corps or {}).get("contentType")).lower() != "html":
        return contenu
    return html.unescape(_BALISE.sub("", contenu)).strip()


def _message(m: dict) -> dict:
    """A message's view: who, when, what, and where to answer it."""
    auteur = m.get("from") or {}
    qui = (auteur.get("user") or auteur.get("application") or {}).get("displayName")
    return {
        "id": m.get("id"),
        "from": qui,
        "createdDateTime": m.get("createdDateTime"),
        "subject": m.get("subject"),
        "text": _texte(m.get("body")),
        "replyToId": m.get("replyToId"),
        "messageType": m.get("messageType"),
        "attachments": len(m.get("attachments") or []),
        "webUrl": m.get("webUrl"),
    }


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.microsoft import TeamsClient

    from ..auth import microsoft as ms_auth

    def _client(renew: bool = False) -> TeamsClient:
        """The Graph client of THIS caller — `renew=True` with a token renewed
        whatever the cache holds."""
        return TeamsClient(token(_SERVICE, renew=renew))

    def _run(fn):
        return run(fn, _LABEL)

    def _meta() -> dict:
        """The vault `meta` of the account this call designates (`_account=`…)."""
        try:
            return ms_auth.resolve_account(access.current_user_sub_or_raise(),
                                           _SERVICE)[1]
        except RuntimeError as e:
            raise bad(str(e))

    def _refus_palier_admin(meta: dict):
        """Reading a channel without the administrator tier: say why, and give the link
        to send to the administrator of the account's directory."""
        try:
            lien = ms_auth.admin_consent_url(access.current_user_sub_or_raise(),
                                             (_SERVICE,), meta.get("tenant"),
                                             connector=_SERVICE)
        except (RuntimeError, ValueError) as e:
            raise bad("Reading channel messages requires your Microsoft admin's approval, "
                      f"and the approval link could not be built: {e}")
        return bad(
            "Reading channel messages requires your Microsoft admin's approval: Teams lets "
            "a person post in a channel and read their chats on their own, but reading a "
            "channel's messages needs a Microsoft 365 administrator to approve oto once "
            "for the whole organization (permission ChannelMessage.Read.All). Send this "
            f"link to that administrator (valid seven days): {lien} — once approved, "
            "retry: nothing to reconnect. Chats (`chat_id`) and posting work without it.")

    def _lecture_canal(lire):
        """`lire(c)` — a read of a channel's messages, under the administrator tier.

        Refused with the approval link when the account does not hold it. The vault
        learns an approval at a token renewal only: when it does not know it yet, the
        token is renewed ONCE before refusing; when it does but Graph answers 403 (a
        token cached before the approval, in this process or another one), renewed
        ONCE before retrying. Never more than one renewal per call."""
        palier = ms_auth.admin_scopes(_SERVICE)
        renouvele = False
        meta = _meta()
        if not ms_auth.has_scopes(meta, palier):
            c = _client(renew=True)
            renouvele = True
            meta = _meta()
            if not ms_auth.has_scopes(meta, palier):
                raise _refus_palier_admin(meta)
        else:
            c = _client()
        try:
            return lire(c)
        except UpstreamHTTPError as e:
            if e.status_code != 403 or renouvele:
                raise_refusal(e, _LABEL)
        c = _client(renew=True)
        return _run(lambda: lire(c))

    @mcp.tool()
    def teams_spaces(
        op: Literal["teams", "channels", "chats"] = "teams",
        team_id: Optional[str] = None,
        limit: int = 50,
        full: bool = False,
    ) -> dict:
        """Where the person can read and post in Microsoft Teams.

        `op`:
        - **"teams"** (default): the teams the person is a member of
          ({teams: [{id, displayName, description}]}).
        - **"channels"**: the channels of `team_id` the person can see
          ({channels: [{id, displayName, membershipType, webUrl}]}).
        - **"chats"**: the person's one-on-one, group and meeting chats, at most
          `limit` ({chats: [{id, topic, chatType, lastUpdatedDateTime}]}).
        A team id + a channel id, or a chat id, is what `teams_message` takes. Ids carry
        `:` and `@` (`19:…@thread.tacv2`): pass them as returned.

        Args:
            op: teams (default) | channels | chats.
            team_id: op="channels" — the team (from op="teams").
            limit: op="chats" — max chats (default 50).
            full: return Graph's raw objects instead of the trimmed view.
        """
        c = _client()
        if op == "teams":
            refuse_ignored(op, team_id=team_id)
            equipes = _run(c.list_joined_teams)
            return {"teams": [raw(t) if full else _equipe(t) for t in equipes],
                    "count": len(equipes)}
        if op == "channels":
            equipe = need(team_id, "team_id", op)
            canaux = _run(lambda: c.list_channels(equipe))
            return {"team_id": equipe, "channels": [raw(ch) if full else _canal(ch)
                                                    for ch in canaux],
                    "count": len(canaux)}
        if op == "chats":
            refuse_ignored(op, team_id=team_id)
            chats = _run(lambda: c.list_chats(limit=limit))
            return {"chats": [raw(ch) if full else _conversation(ch) for ch in chats],
                    "count": len(chats)}
        raise bad("op must be 'teams', 'channels' or 'chats'.")

    @mcp.tool()
    def teams_message(
        op: Literal["list", "replies", "post", "reply"] = "list",
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
        body: Optional[str] = None,
        limit: int = 50,
        full: bool = False,
    ) -> dict:
        """A message in a Teams channel or chat — read, read a thread, post, reply.

        The place is a CHANNEL (`team_id` + `channel_id`, from teams_spaces) OR a CHAT
        (`chat_id`) — exactly one of the two.

        `op`:
        - **"list"** (default): the latest messages, at most `limit` — a channel's root
          messages (without their replies), or a chat's messages.
        - **"replies"**: the replies to the channel message `message_id` (a chat has no
          threads).
        - **"post"**: ⚠️ WRITES — a new message (`body`, markdown): a new thread in the
          channel, or a message in the chat.
        - **"reply"**: ⚠️ WRITES — a reply (`body`) in the thread of the channel
          message `message_id`. In a chat, use op="post".
        ⚠️ A post is visible AT ONCE to the members: Teams has no draft.

        ⚠️ Reading a CHANNEL's messages ("list"/"replies" on a channel) requires your
        Microsoft admin's approval, once for the whole organization: without it the call
        is refused and returns the approval link to send to the administrator. Chats and
        posting do not need it.

        Args:
            op: list (default) | replies | post | reply.
            team_id: a channel's team (with `channel_id`).
            channel_id: the channel (with `team_id`).
            chat_id: the chat, instead of a channel.
            message_id: op="replies"/"reply" — the channel message (from op="list").
            body: op="post"/"reply" — the message, in markdown.
            limit: op="list"/"replies" — max messages (default 50).
            full: return Graph's raw messages instead of the trimmed view.
        """
        canal = team_id is not None or channel_id is not None
        if canal == (chat_id is not None):
            raise bad("name the place: `team_id` + `channel_id` (a channel) OR `chat_id` "
                      "(a chat) — exactly one of the two.")
        if canal:
            equipe = need(team_id, "team_id", op)
            fil = need(channel_id, "channel_id", op)
        vue = raw if full else _message

        def _liste(messages: list) -> dict:
            return {"messages": [vue(m) for m in messages], "count": len(messages)}

        if op in ("list", "replies"):
            refuse_ignored(op, body=body)
            if op == "list":
                refuse_ignored(op, message_id=message_id)
                if not canal:
                    c = _client()
                    return _liste(_run(lambda: c.list_chat_messages(chat_id, limit=limit)))
                return _liste(_lecture_canal(
                    lambda c: c.list_channel_messages(equipe, fil, limit=limit)))
            if not canal:
                raise bad("a chat has no threads: read it with op='list'.")
            racine = need(message_id, "message_id", op)
            return _liste(_lecture_canal(
                lambda c: c.list_replies(equipe, fil, racine, limit=limit)))

        if op in ("post", "reply"):
            contenu = markdown_html(need(body, "body", op))
            c = _client()
            if op == "post":
                refuse_ignored(op, message_id=message_id)
                if canal:
                    return vue(_run(lambda: c.post_channel_message(equipe, fil,
                                                                   html=contenu)))
                return vue(_run(lambda: c.post_chat_message(chat_id, html=contenu)))
            if not canal:
                raise bad("a chat has no threads: post in it with op='post'.")
            racine = need(message_id, "message_id", op)
            return vue(_run(lambda: c.reply_channel_message(equipe, fil, racine,
                                                            html=contenu)))

        raise bad("op must be 'list', 'replies', 'post' or 'reply'.")
