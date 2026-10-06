"""Slack — outbound messaging + reads on behalf of the authenticated user.

Per-user: each user sets their own **user token** (`xoxp-`) on
`/account` (provider `slack`), or an admin grants them the platform key
(bootstrapped from `SLACK_USER_TOKEN`). The key is resolved per call via
`access.resolve_api_key("slack")` — no shared server token in clear text.

An account (a workspace) carries up to two identities: the app (`xoxb-`) and a
person (`xoxp-`). READS are routed by the client (channel → bot, DM →
user). WRITES (post, delete, react) take `author`:
chosen by the caller, refused if ambiguous or without a token — never inferred
(decision of 23/09). The published app, installable in one click, remains a target:
otomata-tech/oto#3.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal, Optional, Union

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, file_content
from ..mcp_errors import McpError
from ..connectors import verify as connector_verify


#: What, in the body of `auth.test`, IDENTIFIES the application — and nothing else.
#: Slack does not return all these fields for all tokens (`bot_id`/`app_id` only exist
#: on the bot side): we relay what it ACTUALLY returned, never a key fabricated as
#: empty. A value we do not have is not returned as its default.
_IDENTITE = ("app_id", "bot_id", "team", "team_id", "url", "user", "user_id")


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001 (config: probe contract)
    """Slack "test the connection" probe (signal #217): a token can be SET,
    authenticate, and still lack the read scopes → `slack_list_channels`
    fails with `missing_scope` and everything else is unreachable (no channel ID).
    Two stages, actionable message: (1) `auth.test` passes with ANY live token
    whatever its scopes → separates "dead token" from "token OK, scope missing";
    (2) a real channel read (`channels:read`) — its `missing_scope` is THE
    diagnostic that was missing.

    ⚠️ **The body of `auth.test` is now RETURNED, no longer thrown away** (signals 802/814, 08/09/2026).
    It was called for its success alone, and it is the response that carried the missing
    fact: the application's identity. A credential replaced with the tokens of ANOTHER
    Slack app authenticates perfectly — and starts from zero on channel memberships,
    which belong to the app, not the key. Lived on org 196: four private
    customer channels unreadable for six days, a `not_in_channel` that is the
    SAME code as a never-joined channel, and a "joined the channel" rewritten six times
    a day in a customer's channel — the only known remedy, for lack of knowing that
    the app had changed. The probe does not judge this change: it returns what is needed to
    notice it, which existed nowhere.

    ⚠️ **One `auth.test` per token set, each with its own.** The previous single call
    went out on the USER token as soon as there was one (`default_as_user`): it
    therefore NEVER identified the bot's app, which is precisely the one that carries
    the memberships. Two tokens = two distinct identities, and confusing them would
    answer "yes, same app" to a bot change.

    ⚠️ **Accepted consequence**: a dead bot token next to a live user token
    now MAKES the probe fail, where it used to pass green without having looked.
    It is one green fewer, not one red more — the bot half of the credential was
    simply never tested, and it is the one that reads channels."""
    from oto.tools.slack.client import SlackClient, SlackError

    bot = (fields.get("bot_token") or "").strip() or None
    user = (fields.get("user_token") or "").strip() or None
    if not bot and not user:  # legacy single-field credential (raw token) → routed by prefix
        raw = next((str(v).strip() for v in fields.values() if str(v or "").strip()), "")
        if raw.startswith("xoxb-"):
            bot = raw
        elif raw:
            user = raw
    if not bot and not user:
        raise ValueError("no Slack token set (bot_token `xoxb-` or user_token `xoxp-`)")

    client = SlackClient(bot_token=bot, user_token=user, default_as_user=bool(user))
    identite: dict = {}
    for genre, jeton in (("bot", bot), ("user", user)):
        if not jeton:
            continue
        try:
            corps = client._request("POST", "auth.test", as_user=(genre == "user"))
        except SlackError as e:
            # The kind is NAMED: with two tokens set, a bare "invalid Slack token"
            # leaves you hunting for which of the two to set again.
            raise ValueError(
                f"invalid Slack token (`{genre}`, {e.error}) — set a valid "
                f"`{'xoxb-' if genre == 'bot' else 'xoxp-'}` again") from None
        identite[genre] = {c: corps[c] for c in _IDENTITE if corps.get(c)}
    try:
        client.list_channels(types="public_channel")
    except SlackError as e:
        if e.error == "missing_scope":
            raise ValueError(
                "Slack token authenticated but SCOPES insufficient: "
                "`channels:read` is missing (without it, no channel ID is discoverable → "
                "`slack_read_history` unreachable). Reinstall the Slack app with "
                "`channels:read`, `groups:read`, `channels:history`, `groups:history`.") from None
        raise ValueError(f"Slack read failed ({e.error})") from None
    return {"identity": identite}


# Where a Slack right is granted. Written ONCE: this procedure is long,
# and it has no place in a docstring (copied into every session, ~470
# tools) — it lives in the refusal, paid for once, at the moment it is needed.
_OU_DONNER_UN_DROIT = (
    "api.slack.com/apps → your app → OAuth & Permissions → Scopes, then REINSTALL "
    "the app in the workspace (an added right is only carried by the token after "
    "reinstallation) and set the new token on /account"
)
# A private channel cannot be joined through ANY API: it is the only action that stays human.
_GESTE_INVITATION = (
    "a human who is already a member of the channel must type `/invite` followed by the name of your Slack app there"
)


# Who WRITES in Slack. An account can carry two identities — the app (bot token
# `xoxb-`) and a person (user token `xoxp-`) — and the client used to route
# writes to the second as soon as it existed: an agent posted in your name without
# having chosen it, or under the app's name without being able to do otherwise. Decision of
# 23/09: the author is CHOSEN. Ambiguous = named refusal, the same rule as for the
# workspace — a message that went out under the wrong name cannot be taken back.
Auteur = Literal["me", "app"]


def _refus_auteur(message: str) -> McpError:
    """CURATED refusal: `McpError` INVALID_PARAMS, never a bare `ValueError`. The
    error taxonomy only returns an exception's text to the agent if it is
    curated; a `ValueError` without an upstream cause becomes "Internal server error",
    message lost and counted as a bug. Lived on the first send in prod (v1.334.0):
    the refusal was right, the agent only read the opacity."""
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _auteur(bot: bool, user: bool, author: Optional[str]) -> bool:
    """`as_user` to use for a write, or a refusal that says what to pass.

    A single token set: it is used, nothing to choose. Both: the caller names
    the author. An author requested without the token that carries it: refusal, never a fallback
    to the other identity."""
    if author is None:
        if bot and user:
            raise _refus_auteur(
                "This workspace carries two Slack identities: specify who writes — "
                "`author=\"me\"` (in your name, user token) or `author=\"app\"` "
                "(under the app's name, bot token). No default is taken: a "
                "message that went out under the wrong name cannot be taken back.")
        return bool(user)
    if author == "me" and not user:
        raise _refus_auteur(
            "`author=\"me\"` impossible: this workspace has no user token "
            "(`xoxp-`). Only the app can write there (`author=\"app\"`), or set a user "
            "token on the connector card.")
    if author == "app" and not bot:
        raise _refus_auteur(
            "`author=\"app\"` impossible: this workspace has no bot token "
            "(`xoxb-`). Only you can write there (`author=\"me\"`), or set the "
            "app's bot token on the connector card.")
    return author == "me"


def _refus(e, channel: Optional[str] = None) -> ValueError:
    """Translates a Slack rejection into an ACTIONABLE refusal — signals #510/#532/#549.

    A `Slack API error: not_in_channel` surfaced as is looks like an outage:
    the scheduled run of signal #549 failed two mornings in a row without
    anyone knowing the missing action was an invitation. Each code carries
    its way out here, and when oto CANNOT do it, it says so instead of letting
    people believe in an incident.

    ⚠️ The refusal is raised `from e`: `error_taxonomy` looks for the upstream status by
    WALKING UP the cause chain. Cutting the chain would make this credential 4xx
    count as a backend bug in Sentry.
    """
    code = getattr(e, "error", None) or "unknown"
    cible = ("`" + channel + "`") if channel else "this channel"

    if code == "missing_scope":
        # Slack ITSELF NAMES the missing right: we relay it, we do not guess
        # it (probed on 28/08: `needed=groups:history` on a private channel thread).
        needed = getattr(e, "needed", None)
        provided = getattr(e, "provided", None)
        if needed:
            msg = ("Slack refuses: the right `" + needed + "` is missing on this "
                   "workspace's token (several separated by a comma = any one is enough).")
        else:
            msg = ("Slack refuses for insufficient rights but does not name which: "
                   "compare the token's rights to the manifest on the connector card.")
        msg += " Grant it at " + _OU_DONNER_UN_DROIT + "."
        if provided:
            msg += " Rights seen by Slack on this token: " + provided + "."
        return ValueError(msg)

    if code == "not_in_channel":
        return ValueError(
            "Slack refuses: the app is not a member of " + cible + ". If the channel is "
            "PUBLIC, call `slack_join_channel` on it and call again. If it is PRIVATE, "
            "no Slack API lets you invite yourself: " + _GESTE_INVITATION + ".")

    if code == "channel_not_found":
        return ValueError(
            "Slack cannot see " + cible + ": either the ID is wrong, or it is a private "
            "channel the app is not in — Slack returns the same code in both cases. "
            "Check the ID with `slack_list_channels`; if it is private, "
            + _GESTE_INVITATION + ".")

    if code == "is_archived":
        return ValueError(
            "Slack refuses: " + cible + " is archived — it can no longer be posted to or "
            "joined. Unarchive it in Slack, or target another channel.")

    # Default: we NAME the Slack code without embellishing. And no "on this
    # channel" when the call did not target one (user lookup, DM opening)
    # — an invented location sends people looking in the wrong place.
    return ValueError("Slack refuses (" + code + ")"
                      + (" on " + cible if channel else "") + ".")


def register(mcp: FastMCP) -> None:
    from oto.tools.slack.client import SlackClient, SlackError
    connector_verify.register("slack", _verify)

    @contextmanager
    def _traduit(channel: Optional[str] = None):
        """Single seam for Slack calls: every upstream rejection comes out actionable.
        `except SlackError` is NARROW — a translation decision, not a safety net
        (the refusal stays loud, and its cause stays in the chain).

        ⚠️ It is a CONTEXT, deliberately: the version-skew probe
        (`test_tools_client_methods_exist`) only counts the attributes **called**
        on the client. Passing the method BY REFERENCE to a wrapper function
        would take the whole module out of its coverage SILENTLY — the hole lived through on
        apollo. Here `client.replies(…)` stays a literal call, hence verified
        against the pinned oto-core."""
        try:
            yield
        except SlackError as e:
            raise _refus(e, channel) from e

    def _jetons() -> tuple[Optional[str], Optional[str], bool]:
        # Multi-field BYO (#25): bot token (xoxb-) and/or user token (xoxp-),
        # resolved by (sub, active org) via the credential cascade (user > active
        # group > active org). ⚠️ It is THIS result that says which identities the
        # account carries — never the client's attributes, which fall back to
        # environment keys when a field is empty.
        rc = access.resolve_credential("slack", want="byo")
        f = rc.fields
        bot = f.get("bot_token") or None
        user = f.get("user_token") or None
        if not bot and not user:
            # Legacy fallback: pre-multi-field credential = single raw token (not
            # JSON → rc.fields empty). Read via rc.key, routed by prefix.
            raw = (rc.key or "").strip()
            if raw.startswith("xoxb-"):
                bot = raw
            elif raw:
                user = raw
        return bot, user, rc.is_platform

    def _client() -> tuple[SlackClient, bool]:
        # READS: the client routes itself (channel → bot, DM → user).
        bot, user, is_platform = _jetons()
        return SlackClient(bot_token=bot, user_token=user,
                           default_as_user=bool(user)), is_platform

    def _ecrivain(author: Optional[str]) -> tuple[SlackClient, bool, str]:
        # WRITES: the author is chosen (`_auteur`), then held for the whole call.
        bot, user, is_platform = _jetons()
        as_user = _auteur(bool(bot), bool(user), author)
        client = SlackClient(bot_token=bot, user_token=user, default_as_user=as_user)
        return client, is_platform, "me" if as_user else "app"

    def _ou(client: SlackClient, channel: str) -> dict:
        """WHERE the message landed, in clear. An ID says neither its name nor who
        we are talking to: on 02/09, a reply meant for Tulina went to JB's
        channel. `shared_externally` says a third party reads. The message has GONE OUT when
        we get here: a read failure is named, it cancels nothing."""
        try:
            info = client.channel_info(channel).get("channel") or {}
        except (SlackError, ValueError) as e:
            return {"id": channel, "unknown": getattr(e, "error", None) or str(e)}
        return {"id": channel, "name": info.get("name"),
                "is_dm": bool(info.get("is_im")),
                "shared_externally": bool(info.get("is_ext_shared"))}

    def _record_if_platform(is_platform: bool) -> None:
        if is_platform:
            access.record_platform_usage("slack")

    @mcp.tool()
    def slack_post_message(
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        author: Optional[Auteur] = None,
    ) -> dict:
        """Send a Slack message to a channel or DM. The response says who wrote
        (`_author`) and where it landed (`_channel`: name, DM, shared externally).

        ⚠️ **Long text is SPLIT, never truncated.** Above ~4,000 characters the
        text goes out as SEVERAL messages: the first one where you asked, each
        later part threaded under it. Nothing is lost. The response then carries
        `ts_all` (every ts, in order) and `split_into` next to `ts` — and `ts` is
        always the FIRST part, the one to reuse to reply in the same thread.
        A response with `ts` alone means one single message went out.

        Read that before re-posting: a message can be deleted but NOT edited, so
        a caller who wrongly concludes it was truncated posts a duplicate instead
        of fixing one. `split_into` is what tells you, without reading the
        channel back — which some procedures forbid as a source.

        Args:
            channel: Channel ID (e.g. C0123456789), DM channel ID (D…), or an
                already-opened conversation. To DM a user by email, call
                `slack_find_user_by_email` + `slack_open_dm` first to get the channel ID.
            text: Message text (Slack mrkdwn supported).
            thread_ts: Parent message ts to reply into a thread.
            author: Who writes — `"me"` (as you, user token) or `"app"` (as the
                Slack app, bot token). Required when the workspace holds both
                tokens; with a single one, it is used and may be omitted.
        """
        client, is_platform, qui = _ecrivain(author)
        with _traduit(channel):
            result = client.post_message(channel, text=text, thread_ts=thread_ts)
        _record_if_platform(is_platform)
        return {**result, "_author": qui, "_channel": _ou(client, channel)}

    @mcp.tool()
    def slack_delete_message(channel: str, ts: str,
                             author: Optional[Auteur] = None) -> dict:
        """Delete a message previously posted — by the same author that posted it.

        Args:
            channel: Channel ID.
            ts: Message timestamp returned by `slack_post_message`.
            author: Who writes — `"me"` (as you, user token) or `"app"` (as the
                Slack app, bot token). Required when the workspace holds both
                tokens; with a single one, it is used and may be omitted.
        """
        client, is_platform, qui = _ecrivain(author)
        with _traduit(channel):
            result = client.delete_message(channel, ts)
        _record_if_platform(is_platform)
        return {**result, "_author": qui}

    @mcp.tool()
    def slack_list_channels(types: str = "public_channel") -> dict:
        """List Slack channels visible to you.

        Args:
            types: Comma-separated channel types — public_channel, private_channel, mpim, im.
        """
        client, is_platform = _client()
        with _traduit():
            result = {"channels": client.list_channels(types=types)}
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def slack_read_history(
        channel: str,
        limit: int = 20,
        cursor: Optional[str] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: bool = False,
    ) -> dict:
        """Read a channel/DM history — TOP-LEVEL messages only.

        Thread replies are NOT here: a parent carries `reply_count`/`latest_reply`
        but no reply body. Read them with `slack_read_thread`.

        Args:
            channel: Channel ID (C…/D…/G…).
            limit: Max messages (capped at 100 by Slack).
            cursor: Pagination cursor returned by a previous call.
            oldest: Only messages after this ts — exclusive. Windows the read
                instead of pulling a full page and filtering client-side.
            latest: Only messages before this ts — exclusive.
            inclusive: Also return the messages sitting exactly on oldest/latest.
        """
        client, is_platform = _client()
        with _traduit(channel):
            result = client.history(channel, limit=limit, cursor=cursor, oldest=oldest,
                                    latest=latest, inclusive=inclusive)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def slack_read_thread(
        channel: str,
        thread_ts: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: bool = False,
    ) -> dict:
        """Read the REPLIES of a Slack thread — where decisions actually land.

        `slack_read_history` returns top-level messages only; a parent with
        `reply_count > 0` hides its replies. This opens it.

        Returns `{parent, replies[], has_more, next_cursor}` — `parent` is kept out
        of `replies` because Slack repeats it on every page. Paginate by feeding
        `next_cursor` back as `cursor`; pages walk the thread newest → oldest.

        Args:
            channel: Channel ID (C…/D…/G…) holding the thread.
            thread_ts: `ts` of the PARENT message (a reply's `thread_ts`). Passing
                a reply's own `ts` is refused, with the parent's ts to retry with.
            limit: Max replies per page (the parent does not count against it).
            cursor: `next_cursor` from a previous call.
            oldest: Only replies after this ts — exclusive.
            latest: Only replies before this ts — exclusive.
            inclusive: Also return replies sitting exactly on oldest/latest.
        """
        client, is_platform = _client()
        with _traduit(channel):
            data = client.replies(channel, thread_ts, limit=limit, cursor=cursor,
                                  oldest=oldest, latest=latest, inclusive=inclusive)
        msgs = data.get("messages") or []
        if not msgs:
            # Slack raises `thread_not_found` on an unknown ts; an empty `ok:true`
            # has never been observed. Refuse rather than return a phantom thread.
            raise ValueError(
                "Slack returns an empty thread for `" + thread_ts + "` in `" + channel
                + "` — check the parent message's ts with `slack_read_history`.")
        parent = msgs[0]
        # ⚠️ Trap probed on 28/08: called with the `ts` of a REPLY, Slack does NOT return
        # the thread — it returns that single message, with `ok:true`. Returned as is, it says
        # "this thread has no reply": the reassuring message that excuses investigating,
        # on the most likely calling error. Slack gives the right ts, we return it.
        vrai_parent = parent.get("thread_ts")
        if vrai_parent and vrai_parent != parent.get("ts"):
            raise ValueError(
                "`thread_ts` points to a REPLY, not the thread's parent: Slack "
                "therefore returned only that message, and nothing of the thread. Call again with "
                "thread_ts=`" + vrai_parent + "`.")
        _record_if_platform(is_platform)
        return {
            "parent": parent,
            "replies": msgs[1:],
            "has_more": bool(data.get("has_more")),
            "next_cursor": (data.get("response_metadata") or {}).get("next_cursor"),
        }

    @mcp.tool()
    def slack_join_channel(channel: str) -> dict:
        """Join a PUBLIC channel so reads and posts stop failing `not_in_channel`.

        A PRIVATE channel cannot be joined by any Slack API — it is refused here,
        naming the human gesture (`/invite`) instead of pretending. Already a
        member → `joined: false, already_member: true`, no call made.

        Args:
            channel: Channel ID (C…). Get it from `slack_list_channels`.
        """
        client, is_platform = _client()
        # Resolve the channel BEFORE attempting: `conversations.info` answers even
        # on a public channel we are not a member of (probed). This is what lets us
        # refuse a private channel without ever pretending to join it —
        # especially since `conversations.join` returns `missing_scope` on a private one as
        # on a wrong ID, so its error distinguishes nothing.
        with _traduit(channel):
            info = client.channel_info(channel).get("channel") or {}
        nom = info.get("name") or channel
        if info.get("is_archived"):
            raise ValueError(
                "#" + nom + " is archived: it can be neither joined nor posted to.")
        if info.get("is_member"):
            return {"channel": info, "joined": False, "already_member": True}
        if info.get("is_private"):
            raise ValueError(
                "#" + nom + " is a private channel: no Slack API lets you invite yourself "
                "(`conversations.join` only applies to public channels). "
                "oto cannot do it for you — " + _GESTE_INVITATION + ".")
        with _traduit(channel):
            client.join_channel(channel)
        _record_if_platform(is_platform)
        return {"channel": info, "joined": True, "already_member": False}

    @mcp.tool()
    def slack_download_file(file_id: str, sheet: Optional[Union[int, str]] = None,
                            max_rows: Optional[int] = None) -> dict:
        """Download a file attached to a Slack message, by its file id.

        Get `file_id` from the `files[]` of a message returned by
        `slack_read_history`. The response depends on the file:
        - **small text** (Markdown/JSON/CSV/plain, ≤256 KB) → returned INLINE:
          `{encoding: "text", content}` — read it directly.
        - **PDF** → its extracted TEXT returned INLINE: `{encoding: "text",
          format: "pdf-text", content, pages, truncated}` plus `raw_url` (+
          `raw_expires_in`), a short-lived signed URL to the original PDF
          (layout, images). A scanned or protected PDF has no text: it comes
          back as a URL, with `text_unavailable` saying why.
        - **binary or large** (zip, image…) → uploaded to temporary storage
          and returned as a short-lived signed URL: `{encoding: "url", url,
          expires_in}` (seconds). Fetch the URL to get the bytes.
        - **spreadsheet (.xlsx)** → returned INLINE as CSV, one section per sheet:
          `{encoding: "text", format: "csv", content, sheets, sheet_names,
          truncated}`. Each section starts with `# sheet=<index> name="…"
          rows_total=… rows_rendered=… truncated=…` then the CSV rows (computed
          values, not formulas; dates ISO 8601). All sheets by default,
          `max_rows` rows each (default 200, max 5000), size-capped: if
          `truncated`, ask one sheet with `sheet` and/or raise `max_rows`; a
          truncated render also carries `raw_url` (+ `raw_expires_in`, seconds): a
          short-lived signed URL to the FULL original file.

        Returns {filename, mimeType, size, encoding, content|url, expires_in?}.

        Args:
            file_id: Slack file id (e.g. F0BG…), from a message's `files[].id`.
            sheet: .xlsx only — the sheet to read, by name or 0-based index (see
                `sheet_names`). Omit for all sheets.
            max_rows: .xlsx only — rows per sheet (default 200, max 5000).
        """
        client, is_platform = _client()
        with _traduit():
            blob = client.fetch_file(file_id)
        sub = access.current_user_sub_or_raise()
        try:
            out = file_content.render_for_agent(
                blob["data"], blob["filename"], blob["mimetype"],
                sub=sub, prefix="slack-files", sheet=sheet, max_rows=max_rows)
        except (file_content.MediaUnavailable, file_content.SpreadsheetError) as e:
            raise ValueError(str(e))
        _record_if_platform(is_platform)
        return out

    @mcp.tool()
    def slack_find_user_by_email(email: str) -> dict:
        """Look up a Slack user by email. Returns the user object (id, name, profile)."""
        client, is_platform = _client()
        with _traduit():
            result = client.find_user_by_email(email)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def slack_open_dm(user: str) -> dict:
        """Open (or return) a DM channel with a user. Returns `{channel: {id: …}}`.

        Args:
            user: Slack user ID (U…). For email lookup, call `slack_find_user_by_email` first.
        """
        client, is_platform = _client()
        with _traduit():
            result = client.open_dm(user)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def slack_add_reaction(channel: str, ts: str, name: str,
                           author: Optional[Auteur] = None) -> dict:
        """Add an emoji reaction to a message.

        Args:
            channel: Channel ID.
            ts: Message timestamp.
            name: Emoji name without colons (e.g. `white_check_mark`).
            author: Who writes — `"me"` (as you, user token) or `"app"` (as the
                Slack app, bot token). Required when the workspace holds both
                tokens; with a single one, it is used and may be omitted.
        """
        client, is_platform, qui = _ecrivain(author)
        with _traduit(channel):
            result = client.add_reaction(channel, ts, name)
        _record_if_platform(is_platform)
        return {**result, "_author": qui}
