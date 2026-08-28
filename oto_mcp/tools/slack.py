"""Slack — outbound messaging + reads on behalf of the authenticated user.

Per-user : chaque user pose son propre **user token** (`xoxp-`) sur
`/account` (provider `slack`), ou un admin lui grant la clé plateforme
(bootstrappée depuis `SLACK_USER_TOKEN`). La clé est résolue par appel via
`access.resolve_api_key("slack")` — pas de token serveur partagé en clair.

Tous les appels passent par le user token (`as_user=True`) : les messages
apparaissent comme l'humain qui a installé l'app. ⚠️ Aujourd'hui ce `xoxp-` est
posé à la main et souvent partagé en clé plateforme → tout le monde poste comme
le même humain. La cible (per-user OAuth : clé app plateforme + `xoxp-` per-user,
+ mode bot `xoxb-` pour les comptes de service) est suivie en
otomata-tech/otomata-private#7.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from fastmcp import FastMCP

from .. import access, connector_verify, file_content


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde)
    """Sonde « tester la connexion » Slack (signal #217) : un token peut être POSÉ,
    authentifier, et pourtant manquer les scopes de lecture → `slack_list_channels`
    échoue en `missing_scope` et tout le reste est inatteignable (pas d'ID de channel).
    Deux étages, message actionnable : (1) `auth.test` passe avec TOUT token vivant
    quels que soient ses scopes → sépare « token mort » de « token OK, scope manquant » ;
    (2) une lecture réelle de channels (`channels:read`) — son `missing_scope` est LE
    diagnostic qui manquait."""
    from oto.tools.slack.client import SlackClient, SlackError

    bot = (fields.get("bot_token") or "").strip() or None
    user = (fields.get("user_token") or "").strip() or None
    if not bot and not user:  # credential mono-champ legacy (token brut) → routé au préfixe
        raw = next((str(v).strip() for v in fields.values() if str(v or "").strip()), "")
        if raw.startswith("xoxb-"):
            bot = raw
        elif raw:
            user = raw
    if not bot and not user:
        raise ValueError("aucun token Slack posé (bot_token `xoxb-` ou user_token `xoxp-`)")

    client = SlackClient(bot_token=bot, user_token=user, default_as_user=bool(user))
    try:
        client._request("POST", "auth.test")
    except SlackError as e:
        raise ValueError(
            f"token Slack invalide ({e.error}) — repose un `xoxb-`/`xoxp-` valide") from None
    try:
        client.list_channels(types="public_channel")
    except SlackError as e:
        if e.error == "missing_scope":
            raise ValueError(
                "token Slack authentifié mais SCOPES insuffisants : il manque "
                "`channels:read` (sans lui, aucun ID de channel n'est découvrable → "
                "`slack_read_history` inatteignable). Réinstalle l'app Slack avec "
                "`channels:read`, `groups:read`, `channels:history`, `groups:history`.") from None
        raise ValueError(f"lecture Slack échouée ({e.error})") from None


# Où se donne un droit Slack. Écrit UNE fois : cette marche à suivre est longue,
# et elle n'a pas sa place dans une docstring (recopiée dans chaque session, ~470
# outils) — elle vit dans le refus, payé une seule fois, au moment où il sert.
_OU_DONNER_UN_DROIT = (
    "api.slack.com/apps → ton app → OAuth & Permissions → Scopes, puis RÉINSTALLE "
    "l'app dans le workspace (un droit ajouté n'est porté par le token qu'à la "
    "réinstallation) et repose le nouveau token sur /account"
)
# Un canal privé ne se rejoint par AUCUNE API : c'est le seul geste qui reste humain.
_GESTE_INVITATION = (
    "un humain déjà membre du canal doit y taper `/invite` suivi du nom de ton app Slack"
)


def _refus(e, channel: Optional[str] = None) -> ValueError:
    """Traduit un rejet Slack en refus ACTIONNABLE — signaux #510/#532/#549.

    Un `Slack API error: not_in_channel` remonté tel quel ressemble à une panne :
    l'exécution planifiée du signal #549 a échoué deux matins de suite sans que
    personne sache que le geste manquant était une invitation. Chaque code porte
    ici la sortie, et quand oto ne PEUT pas la faire, il le dit au lieu de laisser
    croire à un incident.

    ⚠️ Le refus est levé `from e` : `error_taxonomy` cherche le statut amont en
    REMONTANT la chaîne de causes. Couper la chaîne ferait compter ce 4xx de
    credential comme un bug backend dans Sentry.
    """
    code = getattr(e, "error", None) or "unknown"
    cible = ("`" + channel + "`") if channel else "ce canal"

    if code == "missing_scope":
        # Slack NOMME lui-même le droit qui manque : on le relaie, on ne le devine
        # pas (sondé le 28/08 : `needed=groups:history` sur un fil de canal privé).
        needed = getattr(e, "needed", None)
        provided = getattr(e, "provided", None)
        if needed:
            msg = ("Slack refuse : il manque le droit `" + needed + "` sur le token de "
                   "ce workspace (plusieurs séparés par une virgule = l'un suffit).")
        else:
            msg = ("Slack refuse pour droits insuffisants mais ne nomme pas lequel : "
                   "compare les droits du token au manifeste de la fiche du connecteur.")
        msg += " Donne-le sur " + _OU_DONNER_UN_DROIT + "."
        if provided:
            msg += " Droits vus par Slack sur ce token : " + provided + "."
        return ValueError(msg)

    if code == "not_in_channel":
        return ValueError(
            "Slack refuse : l'app n'est pas membre de " + cible + ". Si le canal est "
            "PUBLIC, appelle `slack_join_channel` dessus et rappelle. S'il est PRIVÉ, "
            "aucune API Slack ne permet de s'y inviter : " + _GESTE_INVITATION + ".")

    if code == "channel_not_found":
        return ValueError(
            "Slack ne voit pas " + cible + " : soit l'ID est faux, soit c'est un canal "
            "privé où l'app n'est pas — Slack rend le même code dans les deux cas. "
            "Vérifie l'ID avec `slack_list_channels` ; s'il est privé, "
            + _GESTE_INVITATION + ".")

    if code == "is_archived":
        return ValueError(
            "Slack refuse : " + cible + " est archivé — on n'y poste plus et on ne le "
            "rejoint plus. Désarchive-le dans Slack, ou vise un autre canal.")

    return ValueError("Slack refuse (" + code + ") sur " + cible + ".")


def register(mcp: FastMCP) -> None:
    from oto.tools.slack.client import SlackClient, SlackError
    connector_verify.register("slack", _verify)

    @contextmanager
    def _traduit(channel: Optional[str] = None):
        """Seam unique des appels Slack : tout rejet amont ressort actionnable.
        `except SlackError` est ÉTROIT — une décision de traduction, pas un filet
        (le refus reste bruyant, et sa cause reste dans la chaîne).

        ⚠️ C'est un CONTEXTE, et volontairement : la sonde version-skew
        (`test_tools_client_methods_exist`) ne compte que les attributs **appelés**
        sur le client. Passer la méthode en RÉFÉRENCE à une fonction d'enrobage
        sortirait le module entier de sa couverture EN SILENCE — le trou vécu sur
        apollo. Ici `client.replies(…)` reste un appel littéral, donc vérifié
        contre l'oto-core épinglé."""
        try:
            yield
        except SlackError as e:
            raise _refus(e, channel) from e

    def _client() -> tuple[SlackClient, bool]:
        # BYO multi-champs (#25) : bot token (xoxb-) et/ou user token (xoxp-),
        # résolus par (sub, org active) via la cascade credential (user > groupe
        # actif > org active). default_as_user suit la présence d'un user token
        # (préserve le comportement legacy : un token unique = user token).
        rc = access.resolve_credential("slack", want="byo")
        f = rc.fields
        bot = f.get("bot_token") or None
        user = f.get("user_token") or None
        if not bot and not user:
            # Fallback legacy : credential pré-multichamps = token unique brut (non
            # JSON → rc.fields vide). Lu via rc.key, routé par préfixe.
            raw = (rc.key or "").strip()
            if raw.startswith("xoxb-"):
                bot = raw
            elif raw:
                user = raw
        client = SlackClient(bot_token=bot, user_token=user,
                             default_as_user=bool(user))
        return client, rc.is_platform

    def _record_if_platform(is_platform: bool) -> None:
        if is_platform:
            access.record_platform_usage("slack")

    @mcp.tool()
    def slack_post_message(
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Send a Slack message to a channel or DM (appears as you).

        Args:
            channel: Channel ID (e.g. C0123456789), DM channel ID (D…), or an
                already-opened conversation. To DM a user by email, call
                `slack_find_user_by_email` + `slack_open_dm` first to get the channel ID.
            text: Message text (Slack mrkdwn supported).
            thread_ts: Parent message ts to reply into a thread.
        """
        client, is_platform = _client()
        with _traduit(channel):
            result = client.post_message(channel, text=text, thread_ts=thread_ts)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def slack_delete_message(channel: str, ts: str) -> dict:
        """Delete a message you previously posted.

        Args:
            channel: Channel ID.
            ts: Message timestamp returned by `slack_post_message`.
        """
        client, is_platform = _client()
        with _traduit(channel):
            result = client.delete_message(channel, ts)
        _record_if_platform(is_platform)
        return result

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
            # Slack lève `thread_not_found` sur un ts inconnu ; un `ok:true` vide
            # n'a jamais été observé. Refuser plutôt que rendre un fil fantôme.
            raise ValueError(
                "Slack rend un fil vide pour `" + thread_ts + "` dans `" + channel
                + "` — vérifie le ts du message parent avec `slack_read_history`.")
        parent = msgs[0]
        # ⚠️ Piège sondé le 28/08 : appelé avec le `ts` d'une RÉPONSE, Slack ne rend
        # PAS le fil — il rend ce seul message, en `ok:true`. Rendu tel quel, ça dit
        # « ce fil n'a aucune réponse » : le message rassurant qui dispense d'enquêter,
        # sur l'erreur d'appel la plus probable. Slack donne le bon ts, on le rend.
        vrai_parent = parent.get("thread_ts")
        if vrai_parent and vrai_parent != parent.get("ts"):
            raise ValueError(
                "`thread_ts` pointe une RÉPONSE, pas le parent du fil : Slack n'a "
                "donc rendu que ce message, et rien du fil. Rappelle avec "
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
        # Résoudre le canal AVANT de tenter : `conversations.info` répond y compris
        # sur un canal public dont on n'est pas membre (sondé). C'est ce qui permet
        # de refuser un canal privé sans jamais faire semblant de le rejoindre —
        # d'autant que `conversations.join` rend `missing_scope` sur un privé comme
        # sur un ID faux, donc son erreur ne distingue rien.
        with _traduit(channel):
            info = client.channel_info(channel).get("channel") or {}
        nom = info.get("name") or channel
        if info.get("is_archived"):
            raise ValueError(
                "#" + nom + " est archivé : on ne peut ni le rejoindre ni y poster.")
        if info.get("is_member"):
            return {"channel": info, "joined": False, "already_member": True}
        if info.get("is_private"):
            raise ValueError(
                "#" + nom + " est un canal privé : aucune API Slack ne permet de s'y "
                "inviter (`conversations.join` ne vaut que pour les canaux publics). "
                "oto ne peut pas le faire à ta place — " + _GESTE_INVITATION + ".")
        with _traduit(channel):
            client.join_channel(channel)
        _record_if_platform(is_platform)
        return {"channel": info, "joined": True, "already_member": False}

    @mcp.tool()
    def slack_download_file(file_id: str) -> dict:
        """Download a file attached to a Slack message, by its file id.

        Get `file_id` from the `files[]` of a message returned by
        `slack_read_history`. The response depends on the file:
        - **small text** (Markdown/JSON/CSV/plain, ≤256 KB) → returned INLINE:
          `{encoding: "text", content}` — read it directly.
        - **binary or large** (zip, image, PDF…) → uploaded to temporary storage
          and returned as a short-lived signed URL: `{encoding: "url", url,
          expires_in}` (seconds). Fetch the URL to get the bytes.

        Args:
            file_id: Slack file id (e.g. F0BG…), from a message's `files[].id`.

        Returns {filename, mimeType, size, encoding, content|url, expires_in?}.
        """
        client, is_platform = _client()
        with _traduit():
            blob = client.fetch_file(file_id)
        sub = access.current_user_sub_or_raise()
        try:
            out = file_content.render_for_agent(
                blob["data"], blob["filename"], blob["mimetype"],
                sub=sub, prefix="slack-files")
        except file_content.MediaUnavailable as e:
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
    def slack_add_reaction(channel: str, ts: str, name: str) -> dict:
        """Add an emoji reaction to a message.

        Args:
            channel: Channel ID.
            ts: Message timestamp.
            name: Emoji name without colons (e.g. `white_check_mark`).
        """
        client, is_platform = _client()
        with _traduit(channel):
            result = client.add_reaction(channel, ts, name)
        _record_if_platform(is_platform)
        return result
