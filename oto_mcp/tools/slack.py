"""Slack — outbound messaging + reads on behalf of the authenticated user.

Per-user : chaque user pose son propre **user token** (`xoxp-`) sur
`/account` (provider `slack`), ou un admin lui grant la clé plateforme
(bootstrappée depuis `SLACK_USER_TOKEN`). La clé est résolue par appel via
`access.resolve_api_key("slack")` — pas de token serveur partagé en clair.

Un compte (un workspace) porte jusqu'à deux identités : l'app (`xoxb-`) et une
personne (`xoxp-`). Les LECTURES sont routées par le client (canal → bot, DM →
utilisateur). Les ÉCRITURES (poster, supprimer, réagir) prennent `author` :
choisi par l'appelant, refusé s'il est ambigu ou sans jeton — jamais déduit
(décision du 23/09). L'app publiée, installable en un clic, reste une cible :
otomata-tech/oto#3.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal, Optional, Union

from fastmcp import FastMCP

from .. import access, file_content
from ..connectors import verify as connector_verify


#: Ce qui, dans le corps d'`auth.test`, IDENTIFIE l'application — et rien d'autre.
#: Slack ne rend pas tous ces champs pour tous les jetons (`bot_id`/`app_id` n'existent
#: que côté bot) : on relaie ce qu'il a RÉELLEMENT rendu, jamais une clé fabriquée à
#: vide. Une valeur qu'on n'a pas ne se rend pas par son défaut.
_IDENTITE = ("app_id", "bot_id", "team", "team_id", "url", "user", "user_id")


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001 (config: contrat de sonde)
    """Sonde « tester la connexion » Slack (signal #217) : un token peut être POSÉ,
    authentifier, et pourtant manquer les scopes de lecture → `slack_list_channels`
    échoue en `missing_scope` et tout le reste est inatteignable (pas d'ID de channel).
    Deux étages, message actionnable : (1) `auth.test` passe avec TOUT token vivant
    quels que soient ses scopes → sépare « token mort » de « token OK, scope manquant » ;
    (2) une lecture réelle de channels (`channels:read`) — son `missing_scope` est LE
    diagnostic qui manquait.

    ⚠️ **Le corps d'`auth.test` est RENDU, plus jeté** (signaux 802/814, 08/09/2026).
    Il était appelé pour son seul succès, et c'est la réponse qui portait le fait
    manquant : l'identité de l'application. Un credential remplacé par les jetons d'une
    AUTRE app Slack authentifie parfaitement — et repart de zéro sur les appartenances
    de canaux, qui appartiennent à l'app, pas à la clé. Vécu sur l'org 196 : quatre
    canaux clients privés illisibles six jours durant, un `not_in_channel` qui est le
    MÊME code qu'un canal jamais rejoint, et un « a rejoint le canal » réécrit six fois
    par jour dans le canal d'un client — le seul remède connu, faute de savoir que
    l'app avait changé. La sonde ne juge pas ce changement : elle rend de quoi le
    constater, ce qui n'existait nulle part.

    ⚠️ **Un `auth.test` par jeton posé, chacun avec le sien.** L'appel unique d'avant
    partait sur le jeton UTILISATEUR dès qu'il y en avait un (`default_as_user`) : il
    n'identifiait donc JAMAIS l'app du bot, qui est précisément celle qui porte les
    appartenances. Deux jetons = deux identités distinctes, et les confondre ferait
    répondre « oui, même app » à un changement de bot.

    ⚠️ **Conséquence assumée** : un jeton bot mort à côté d'un jeton utilisateur vivant
    FAIT désormais échouer la sonde, là où elle passait au vert sans l'avoir regardé.
    C'est un vert de moins, pas un rouge de plus — la moitié bot du credential n'était
    tout simplement jamais testée, et c'est elle qui lit les canaux."""
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
    identite: dict = {}
    for genre, jeton in (("bot", bot), ("user", user)):
        if not jeton:
            continue
        try:
            corps = client._request("POST", "auth.test", as_user=(genre == "user"))
        except SlackError as e:
            # Le genre est NOMMÉ : avec deux jetons posés, « token Slack invalide »
            # tout court laisse chercher lequel des deux reposer.
            raise ValueError(
                f"token Slack invalide (`{genre}`, {e.error}) — repose un "
                f"`{'xoxb-' if genre == 'bot' else 'xoxp-'}` valide") from None
        identite[genre] = {c: corps[c] for c in _IDENTITE if corps.get(c)}
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
    return {"identity": identite}


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


# Qui ÉCRIT dans Slack. Un compte peut porter deux identités — l'app (jeton de bot
# `xoxb-`) et une personne (jeton utilisateur `xoxp-`) — et le client routait les
# écritures sur la seconde dès qu'elle existait : un agent postait en ton nom sans
# l'avoir choisi, ou sous le nom de l'app sans pouvoir faire autrement. Décision du
# 23/09 : l'auteur se CHOISIT. Ambigu = refus nommé, la même règle que pour le
# workspace — un message parti sous le mauvais nom ne se reprend pas.
Auteur = Literal["me", "app"]


def _auteur(bot: bool, user: bool, author: Optional[str]) -> bool:
    """`as_user` à employer pour une écriture, ou un refus qui dit quoi passer.

    Un seul jeton posé : il sert, rien à choisir. Les deux : l'appelant nomme
    l'auteur. Un auteur demandé sans le jeton qui le porte : refus, jamais un repli
    sur l'autre identité."""
    if author is None:
        if bot and user:
            raise ValueError(
                "Ce workspace porte deux identités Slack : précise qui écrit — "
                "`author=\"me\"` (en ton nom, jeton utilisateur) ou `author=\"app\"` "
                "(sous le nom de l'app, jeton de bot). Aucun défaut n'est pris : un "
                "message parti sous le mauvais nom ne se reprend pas.")
        return bool(user)
    if author == "me" and not user:
        raise ValueError(
            "`author=\"me\"` impossible : ce workspace n'a pas de jeton utilisateur "
            "(`xoxp-`). Seule l'app peut y écrire (`author=\"app\"`), ou pose un jeton "
            "utilisateur sur la fiche du connecteur.")
    if author == "app" and not bot:
        raise ValueError(
            "`author=\"app\"` impossible : ce workspace n'a pas de jeton de bot "
            "(`xoxb-`). Seule ta personne peut y écrire (`author=\"me\"`), ou pose le "
            "jeton de bot de l'app sur la fiche du connecteur.")
    return author == "me"


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

    # Défaut : on NOMME le code Slack sans rien broder autour. Et pas de « sur ce
    # canal » quand l'appel n'en visait pas un (recherche d'utilisateur, ouverture
    # de DM) — une localisation inventée envoie chercher au mauvais endroit.
    return ValueError("Slack refuse (" + code + ")"
                      + (" sur " + cible if channel else "") + ".")


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

    def _jetons() -> tuple[Optional[str], Optional[str], bool]:
        # BYO multi-champs (#25) : bot token (xoxb-) et/ou user token (xoxp-),
        # résolus par (sub, org active) via la cascade credential (user > groupe
        # actif > org active). ⚠️ C'est CE résultat qui dit quelles identités le
        # compte porte — jamais les attributs du client, qui retombe sur des clés
        # d'environnement quand un champ est vide.
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
        return bot, user, rc.is_platform

    def _client() -> tuple[SlackClient, bool]:
        # LECTURES : le client route lui-même (canal → bot, DM → utilisateur).
        bot, user, is_platform = _jetons()
        return SlackClient(bot_token=bot, user_token=user,
                           default_as_user=bool(user)), is_platform

    def _ecrivain(author: Optional[str]) -> tuple[SlackClient, bool, str]:
        # ÉCRITURES : l'auteur est choisi (`_auteur`), puis tenu pour tout l'appel.
        bot, user, is_platform = _jetons()
        as_user = _auteur(bool(bot), bool(user), author)
        client = SlackClient(bot_token=bot, user_token=user, default_as_user=as_user)
        return client, is_platform, "me" if as_user else "app"

    def _ou(client: SlackClient, channel: str) -> dict:
        """OÙ le message est arrivé, en clair. Un ID ne dit ni son nom ni à qui on
        parle : le 02/09, une réponse destinée à Tulina est partie sur le canal de
        JB. `shared_externally` dit qu'un tiers lit. Le message est PARTI quand on
        arrive ici : un échec de lecture se nomme, il n'annule rien."""
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
    def slack_download_file(file_id: str, sheet: Optional[Union[int, str]] = None,
                            max_rows: Optional[int] = None) -> dict:
        """Download a file attached to a Slack message, by its file id.

        Get `file_id` from the `files[]` of a message returned by
        `slack_read_history`. The response depends on the file:
        - **small text** (Markdown/JSON/CSV/plain, ≤256 KB) → returned INLINE:
          `{encoding: "text", content}` — read it directly.
        - **binary or large** (zip, image, PDF…) → uploaded to temporary storage
          and returned as a short-lived signed URL: `{encoding: "url", url,
          expires_in}` (seconds). Fetch the URL to get the bytes.
        - **spreadsheet (.xlsx)** → returned INLINE as CSV, one section per sheet:
          `{encoding: "text", format: "csv", content, sheets, sheet_names,
          truncated}`. Each section starts with `# sheet=<index> name="…"
          rows_total=… rows_rendered=… truncated=…` then the CSV rows (computed
          values, not formulas; dates ISO 8601). All sheets by default,
          `max_rows` rows each (default 200, max 5000), size-capped: if
          `truncated`, ask one sheet with `sheet` and/or raise `max_rows`.

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
