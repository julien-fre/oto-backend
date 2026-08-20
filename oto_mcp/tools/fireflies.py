"""Fireflies.ai — transcripts, live meeting control, AskFred Q&A, org data.

Wrappe `oto.tools.fireflies.client.FirefliesClient` (GraphQL, un seul
endpoint, Bearer). keyed `api_key`, byo-only (pas de clé plateforme) : chaque
user/org pose SA clé Fireflies (app.fireflies.ai/integrations/api).

**7 tools, un par objet métier** (silae, ADR 0047) :
- `fireflies_transcript` — le gros morceau : list/get/delete un transcript,
  transcrire un fichier audio distant (`upload`) ou local (`create_upload_url`
  + `confirm_upload`, flux non documenté par Fireflies, découvert en live via
  introspection), renommer/reprivatiser/rechanneler, partager/révoquer
  l'accès. op=list/get/delete/upload/update_title/update_privacy/
  update_channel/share/unshare/create_upload_url/confirm_upload.
- `fireflies_live_meeting` — piloter une réunion EN COURS : lister les
  réunions actives, y injecter le bot Fireflies, lister/créer des action
  items live, créer un soundbite live, changer l'état (pause/reprise).
  op=list_active/add_bot/list_action_items/create_action_item/
  create_soundbite/update_state.
- `fireflies_askfred` — l'assistant Q&A de Fireflies sur un ou plusieurs
  transcripts : lister/lire/créer/continuer/supprimer un thread.
  op=list_threads/get_thread/create_thread/continue_thread/delete_thread.
- `fireflies_user` — profil utilisateur (soi ou un tiers), annuaire complet,
  groupes, changement de rôle admin. op=get/list/groups/set_role.
- `fireflies_channel` — canaux (organisation des réunions). op=get/list.
- `fireflies_bite` — Soundbites (extraits courts d'une réunion). op=get/
  list/create.
- `fireflies_org` — lectures diverses sans objet dédié : contacts, analytics
  d'équipe/individuelles, sorties d'AI Apps, logs d'exécution de règles
  d'automatisation, journal d'audit (ces deux derniers = Enterprise only).
  op=contacts/analytics/apps/rule_executions/audit_events.

**Webhooks = dashboard-only, volontairement absents d'ici.** Fireflies V1
(app.fireflies.ai/settings) ET V2 (app.fireflies.ai/integrations/api/webhook)
se configurent EXCLUSIVEMENT via l'interface web — il n'existe aucune query
ni mutation GraphQL pour créer/lister/supprimer un abonnement webhook
(confirmé sur la doc). Contrairement à Granola/Grain, ce connecteur n'a donc
pas de tool `*_webhook`/`*_hook` : ce n'est pas un trou de couverture, c'est
un reflet exact de ce que l'API expose.

**Aucun param n'est retenu au silence** : un `op` qui ne reconnaît pas un
argument fourni REFUSE plutôt que de l'ignorer (silae `_refuse_ignored`).

⚠️ **Aucun spec machine-readable n'existe pour cette API**
(`docs.fireflies.ai/api-reference/openapi.json` est listé dans `llms.txt`
mais rend 404 "Asset not found" au fetch, confirmé via `curl` ET WebFetch) —
tout ici vient d'une lecture de pages de doc, jamais testé en live (pas de
clé Fireflies fournie dans cette session). Voir le docstring de
`FirefliesClient` pour le détail (dont la résolution du champ
`sentence`→`sentences`).

**GraphQL ≠ REST côté erreurs** : une erreur amont peut arriver en HTTP 200
avec un tableau `errors[]` (`FirefliesGraphQLError`) OU en HTTP >= 400
(`UpstreamHTTPError`, ex. clé invalide) — `_upstream_message` gère les deux.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention,
    pas un détail — sinon un param mal placé serait silencieusement ignoré."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    from oto.tools.fireflies import FirefliesGraphQLError
    from oto.tools.common.errors import UpstreamHTTPError

    if isinstance(e, UpstreamHTTPError):
        status = e.status_code
        if status in (401, 403):
            return (f"Fireflies a rejeté la clé API (HTTP {status}) — vérifie la clé posée "
                     "sur ce connecteur (app.fireflies.ai/integrations/api).")
        if status == 429:
            return ("Fireflies : trop de requêtes (429) — limite par palier (Free 50/jour, "
                     "Pro 500/jour, Business/Enterprise 60/min ; certaines mutations ont "
                     "leur propre limite, ex. deleteTranscript 10/min). Réessaie plus tard.")
        if status in (500, 502, 503, 504):
            return f"Fireflies est momentanément indisponible (HTTP {status}) — réessaie plus tard."
        return f"Fireflies a refusé la requête (HTTP {status}) : {e.body}"

    if isinstance(e, FirefliesGraphQLError):
        code = e.code or ""
        if code in ("object_not_found",):
            return f"Fireflies : ressource introuvable — {e.message}"
        if code in ("not_in_team", "not_authorized"):
            return f"Fireflies : accès refusé — {e.message}"
        if code == "require_elevated_privilege":
            return f"Fireflies : privilèges insuffisants (admin/organisateur requis) — {e.message}"
        if code == "too_many_requests":
            return f"Fireflies : trop de requêtes — {e.message}"
        return f"Fireflies a refusé la requête : {e.message}"

    return str(e)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : le profil du porteur de la clé, la
    plus légère des lectures (pas de filtre, un seul objet)."""
    from oto.tools.fireflies.client import FirefliesClient
    FirefliesClient(api_key=fields["key"]).get_user()


def register(mcp: FastMCP) -> None:
    from oto.tools.fireflies.client import FirefliesClient
    from oto.tools.fireflies import FirefliesGraphQLError
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("fireflies", _verify)

    def _client() -> FirefliesClient:
        key, _ = access.resolve_api_key("fireflies")
        return FirefliesClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except (UpstreamHTTPError, FirefliesGraphQLError) as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Transcript & meeting — CRUD + upload + share
    # ================================================================

    @mcp.tool()
    def fireflies_transcript(
        op: Literal["list", "get", "delete", "upload", "update_title", "update_privacy",
                     "update_channel", "share", "unshare",
                     "create_upload_url", "confirm_upload"] = "list",
        transcript_id: Optional[str] = None,
        transcript_ids: Optional[List[str]] = None,
        keyword: Optional[str] = None,
        scope: Optional[Literal["title", "sentences", "all"]] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
        host_email: Optional[str] = None,
        user_id: Optional[str] = None,
        mine: Optional[bool] = None,
        organizers: Optional[List[str]] = None,
        participants: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
        url: Optional[str] = None,
        title: Optional[str] = None,
        webhook: Optional[str] = None,
        custom_language: Optional[str] = None,
        save_video: Optional[bool] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        privacy: Optional[Literal["link", "owner", "participants",
                                   "participatingteammates", "teammatesandparticipants",
                                   "teammates"]] = None,
        emails: Optional[List[str]] = None,
        expiry_days: Optional[int] = None,
        email: Optional[str] = None,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
        meeting_id: Optional[str] = None,
    ) -> object:
        """A meeting transcript — list/search, fetch one (full sentences +
        summary + analytics), delete, transcribe a remote audio/video file,
        rename, change privacy, assign a channel, share/revoke access, or
        get/confirm a direct-file-upload URL.

        Args:
            op: "list" (default, search) | "get" | "delete" | "upload"
                (transcribe a PUBLIC URL) | "update_title" | "update_privacy" |
                "update_channel" | "share" | "unshare" | "create_upload_url"
                (get a one-time URL for a LOCAL file, undocumented by
                Fireflies but confirmed live — pair with the byte-PUT done
                outside this tool, then "confirm_upload") | "confirm_upload".
            transcript_id: REQUIRED by "get"/"delete"/"update_title"/
                "update_privacy"/"share"/"unshare" (the last two call it the
                meeting id — same thing in Fireflies).
            transcript_ids: REQUIRED by "update_channel" — 1-5 ids,
                all-or-nothing (any invalid id fails the whole call). This
                tool checks `channel_id` against `fireflies_channel(op="list")`
                before calling Fireflies and refuses an unknown one — the API
                itself does NOT validate it (a typo would silently stick as
                the transcript's channel, live-confirmed, and there is no way
                to unset a channel once assigned, only replace it).
            keyword: op="list" only — searches title (or spoken words too,
                depending on `scope`).
            scope: op="list" only — "title" (default when `keyword` is set
                without it, live-confirmed), "sentences" (search spoken
                words), or "all". REQUIRES `keyword` to also be set —
                Fireflies rejects `scope` passed alone.
            from_date/to_date/limit/skip/host_email/user_id/mine/organizers/
                participants/channel_id: op="list" only — further filters.
                `limit` caps at 50. `mine=True` scopes to the key owner's own
                meetings. (`channel_id` here filters op="list"'s results —
                unrelated to `update_channel`'s target channel above.)
            url: REQUIRED by "upload" — HTTPS URL of the media file, must be
                publicly accessible.
            title: "upload" (meeting title) or "update_title" (new title,
                admin-only per the docs — live-tested by the connector's
                author and NOT rejected, so treat "admin-only" as unverified).
            webhook/custom_language/save_video/attendees: op="upload" only —
                `attendees` is a list of {"displayName"?, "email"?,
                "phoneNumber"?}.
            privacy: REQUIRED by "update_privacy" — the 6-value
                `MeetingPrivacy` enum (the docs only ever showed 5; missing
                `participatingteammates`).
            emails: REQUIRED by "share" — up to 50 addresses.
            expiry_days: op="share" only, optional.
            email: REQUIRED by "unshare" — whose access to revoke.
            content_type: REQUIRED by "create_upload_url" — MIME type of the
                local file (e.g. "audio/mpeg").
            file_size: REQUIRED by "create_upload_url" — size in bytes.
            meeting_id: REQUIRED by "confirm_upload" — the `meeting_id` a
                prior "create_upload_url" call returned.

        Note: the byte-PUT step between "create_upload_url" and
        "confirm_upload" is NOT a tool here — a raw binary payload has no
        natural fit for a JSON tool call (same reasoning as Grain's
        `grain_recording_file`). `oto.tools.fireflies.client.FirefliesClient
        .upload_file_bytes` is available for scripted use outside the agent
        loop.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='get' pour un transcript précis",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            if scope is not None and keyword is None:
                raise _bad("op='list' : `scope` requiert `keyword` (Fireflies refuse `scope` seul)")
            filters = {k: v for k, v in dict(
                keyword=keyword, scope=scope, fromDate=from_date, toDate=to_date, limit=limit,
                skip=skip, host_email=host_email, user_id=user_id, mine=mine,
                organizers=organizers, participants=participants, channel_id=channel_id,
            ).items() if v is not None}
            return _run(lambda: client.list_transcripts(**filters))
        _refuse_ignored(op, "ces filtres/paramètres de recherche ne valent que pour op='list'",
                         keyword=keyword, scope=scope, from_date=from_date, to_date=to_date,
                         host_email=host_email, mine=mine, organizers=organizers,
                         participants=participants)
        if op == "get":
            if not transcript_id:
                raise _bad("op='get' requiert `transcript_id`")
            _refuse_ignored(op, "op='get' ne prend que `transcript_id`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.get_transcript(transcript_id))
        if op == "delete":
            if not transcript_id:
                raise _bad("op='delete' requiert `transcript_id`")
            _refuse_ignored(op, "op='delete' ne prend que `transcript_id`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.delete_transcript(transcript_id))
        if op == "upload":
            _refuse_ignored(op, "op='upload' ne cible pas un transcript existant",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             privacy=privacy, emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            if not url:
                raise _bad("op='upload' requiert `url`")
            kwargs = {k: v for k, v in dict(
                title=title, webhook=webhook, custom_language=custom_language,
                save_video=save_video, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.upload_audio(url, **kwargs))
        if op == "update_title":
            if not transcript_id or not title:
                raise _bad("op='update_title' requiert `transcript_id` et `title`")
            _refuse_ignored(op, "op='update_title' ne prend que `transcript_id`/`title`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, webhook=webhook,
                             custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails,
                             expiry_days=expiry_days, email=email, content_type=content_type,
                             file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.update_meeting_title(transcript_id, title))
        if op == "update_privacy":
            if not transcript_id or not privacy:
                raise _bad("op='update_privacy' requiert `transcript_id` et `privacy`")
            _refuse_ignored(op, "op='update_privacy' ne prend que `transcript_id`/`privacy`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.update_meeting_privacy(transcript_id, privacy))
        if op == "update_channel":
            if not transcript_ids or not channel_id:
                raise _bad("op='update_channel' requiert `transcript_ids` et `channel_id`")
            _refuse_ignored(op, "op='update_channel' ne prend que `transcript_ids`/`channel_id`",
                             transcript_id=transcript_id, limit=limit, skip=skip, user_id=user_id,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            known_channel_ids = {c["id"] for c in _run(lambda: client.list_channels())}
            if channel_id not in known_channel_ids:
                raise _bad(
                    f"op='update_channel' : channel_id={channel_id!r} introuvable ou "
                    "inaccessible avec cette clé (absent de fireflies_channel(op='list') — "
                    "peut aussi être un canal privé dont tu n'es pas membre) — Fireflies "
                    "n'écrit PAS d'erreur si l'id est invalide, il stocke la valeur telle "
                    "quelle, sur un canal qu'on ne peut plus retirer ensuite. Vérifie l'id "
                    "d'abord.")
            return _run(lambda: client.update_meeting_channel(transcript_ids, channel_id))
        if op == "share":
            if not transcript_id or not emails:
                raise _bad("op='share' requiert `transcript_id` (l'id de la réunion) et `emails`")
            _refuse_ignored(op, "op='share' ne prend que `transcript_id`/`emails`/`expiry_days`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, email=email,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.share_meeting(transcript_id, emails, expiry_days=expiry_days))
        if op == "unshare":
            if not transcript_id or not email:
                raise _bad("op='unshare' requiert `transcript_id` (l'id de la réunion) et `email`")
            _refuse_ignored(op, "op='unshare' ne prend que `transcript_id`/`email`",
                             transcript_ids=transcript_ids, limit=limit, skip=skip,
                             channel_id=channel_id, user_id=user_id, url=url, title=title,
                             webhook=webhook, custom_language=custom_language, save_video=save_video,
                             attendees=attendees, privacy=privacy, emails=emails, expiry_days=expiry_days,
                             content_type=content_type, file_size=file_size, meeting_id=meeting_id)
            return _run(lambda: client.revoke_shared_meeting_access(transcript_id, email))
        if op == "create_upload_url":
            _refuse_ignored(op, "op='create_upload_url' ne cible pas un transcript existant",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             url=url, webhook=webhook, save_video=save_video, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email, meeting_id=meeting_id)
            if content_type is None or file_size is None:
                raise _bad("op='create_upload_url' requiert `content_type` et `file_size`")
            kwargs = {k: v for k, v in dict(
                title=title, custom_language=custom_language, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.create_upload_url(content_type, file_size, **kwargs))
        if op == "confirm_upload":
            _refuse_ignored(op, "op='confirm_upload' ne prend que `meeting_id`",
                             transcript_id=transcript_id, transcript_ids=transcript_ids,
                             limit=limit, skip=skip, channel_id=channel_id, user_id=user_id,
                             url=url, title=title, webhook=webhook, custom_language=custom_language,
                             save_video=save_video, attendees=attendees, privacy=privacy,
                             emails=emails, expiry_days=expiry_days, email=email,
                             content_type=content_type, file_size=file_size)
            if not meeting_id:
                raise _bad("op='confirm_upload' requiert `meeting_id` (rendu par 'create_upload_url')")
            return _run(lambda: client.confirm_upload(meeting_id))
        raise _bad("op inconnu")

    # ================================================================
    # Live meeting control
    # ================================================================

    @mcp.tool()
    def fireflies_live_meeting(
        op: Literal["list_active", "add_bot", "list_action_items", "create_action_item",
                     "create_soundbite", "update_state"] = "list_active",
        meeting_id: Optional[str] = None,
        email: Optional[str] = None,
        states: Optional[List[Literal["active", "paused"]]] = None,
        meeting_link: Optional[str] = None,
        title: Optional[str] = None,
        meeting_password: Optional[str] = None,
        duration: Optional[int] = None,
        language: Optional[str] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        prompt: Optional[str] = None,
        action: Optional[Literal["pause_recording", "resume_recording"]] = None,
    ) -> object:
        """Control a meeting the Fireflies bot is CURRENTLY recording — list
        active meetings, drop the bot into one, capture live action items /
        soundbites, or change its recording state.

        Args:
            op: "list_active" (default) | "add_bot" | "list_action_items" |
                "create_action_item" | "create_soundbite" | "update_state".
            meeting_id: REQUIRED by "list_action_items"/"create_action_item"/
                "create_soundbite"/"update_state".
            email: op="list_active" only — non-admins can only pass their own.
            states: op="list_active" only — "active"/"paused", defaults to both.
            meeting_link: REQUIRED by "add_bot" — Zoom/Meet/Teams/etc URL.
            title/meeting_password/duration/language/attendees: op="add_bot"
                only — `duration` in minutes (15-120, default 60).
            prompt: REQUIRED by "create_action_item"/"create_soundbite" —
                natural-language description; both need AI credits, rate
                limited 10/hour.
            action: REQUIRED by "update_state" — "pause_recording" or
                "resume_recording" (the full `MeetingStateAction` enum,
                confirmed live via introspection 2026-08-20 — the docs never
                enumerated it). Rate limited 10/hour. `add_bot` itself is
                rate limited 3/20min.
        """
        client = _client()
        if op == "list_active":
            _refuse_ignored(op, "op='list_active' ne prend que `email`/`states`",
                             meeting_id=meeting_id, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt, action=action)
            return _run(lambda: client.list_active_meetings(email=email, states=states))
        if op == "add_bot":
            _refuse_ignored(op, "op='add_bot' ne prend pas ces paramètres",
                             meeting_id=meeting_id, email=email, states=states, prompt=prompt, action=action)
            if not meeting_link:
                raise _bad("op='add_bot' requiert `meeting_link`")
            kwargs = {k: v for k, v in dict(
                title=title, meeting_password=meeting_password, duration=duration,
                language=language, attendees=attendees,
            ).items() if v is not None}
            return _run(lambda: client.add_to_live_meeting(meeting_link, **kwargs))
        if not meeting_id:
            raise _bad(f"op={op!r} requiert `meeting_id`")
        if op == "list_action_items":
            _refuse_ignored(op, "op='list_action_items' ne prend que `meeting_id`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt, action=action)
            return _run(lambda: client.list_live_action_items(meeting_id))
        if op in ("create_action_item", "create_soundbite"):
            _refuse_ignored(op, f"op={op!r} ne prend que `meeting_id`/`prompt`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, action=action)
            if not prompt:
                raise _bad(f"op={op!r} requiert `prompt`")
            if op == "create_action_item":
                return _run(lambda: client.create_live_action_item(meeting_id, prompt))
            return _run(lambda: client.create_live_soundbite(meeting_id, prompt))
        if op == "update_state":
            _refuse_ignored(op, "op='update_state' ne prend que `meeting_id`/`action`",
                             email=email, states=states, meeting_link=meeting_link, title=title,
                             meeting_password=meeting_password, duration=duration,
                             language=language, attendees=attendees, prompt=prompt)
            if not action:
                raise _bad("op='update_state' requiert `action`")
            return _run(lambda: client.update_meeting_state(meeting_id, action))
        raise _bad("op inconnu")

    # ================================================================
    # AskFred — Q&A threads over one or more transcripts
    # ================================================================

    @mcp.tool()
    def fireflies_askfred(
        op: Literal["list_threads", "get_thread", "create_thread",
                     "continue_thread", "delete_thread"] = "list_threads",
        thread_id: Optional[str] = None,
        transcript_id: Optional[str] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        response_language: Optional[str] = None,
        format_mode: Optional[Literal["markdown", "plaintext"]] = None,
    ) -> object:
        """AskFred — Fireflies' AI Q&A over meeting transcripts. List/read
        threads, ask a new question, follow up in an existing thread, or
        delete one.

        Args:
            op: "list_threads" (default) | "get_thread" | "create_thread" |
                "continue_thread" | "delete_thread".
            thread_id: REQUIRED by "get_thread"/"continue_thread"/"delete_thread".
            transcript_id: op="list_threads" (optional filter) or
                "create_thread" (optional — scopes the question to one
                meeting; if set, `filters` is ignored).
            query: REQUIRED by "create_thread"/"continue_thread" — the
                question, max 2000 chars.
            filters: op="create_thread" only, ignored if `transcript_id` is
                set — {"start_time"?, "end_time"?, "channel_ids"?,
                "organizers"?, "participants"?, "transcript_ids"?} to search
                across multiple meetings.
            response_language/format_mode: "create_thread"/"continue_thread"
                only.
        """
        client = _client()
        if op == "list_threads":
            _refuse_ignored(op, "op='list_threads' ne prend que `transcript_id`",
                             thread_id=thread_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.list_askfred_threads(transcript_id=transcript_id))
        if op == "get_thread":
            if not thread_id:
                raise _bad("op='get_thread' requiert `thread_id`")
            _refuse_ignored(op, "op='get_thread' ne prend que `thread_id`",
                             transcript_id=transcript_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.get_askfred_thread(thread_id))
        if op == "create_thread":
            _refuse_ignored(op, "op='create_thread' ne prend pas `thread_id`", thread_id=thread_id)
            if not query:
                raise _bad("op='create_thread' requiert `query`")
            kwargs = {k: v for k, v in dict(
                transcript_id=transcript_id, filters=filters, response_language=response_language,
                format_mode=format_mode,
            ).items() if v is not None}
            return _run(lambda: client.create_askfred_thread(query, **kwargs))
        if op == "continue_thread":
            _refuse_ignored(op, "op='continue_thread' ne prend pas `transcript_id`/`filters`",
                             transcript_id=transcript_id, filters=filters)
            if not thread_id or not query:
                raise _bad("op='continue_thread' requiert `thread_id` et `query`")
            kwargs = {k: v for k, v in dict(
                response_language=response_language, format_mode=format_mode,
            ).items() if v is not None}
            return _run(lambda: client.continue_askfred_thread(thread_id, query, **kwargs))
        if op == "delete_thread":
            if not thread_id:
                raise _bad("op='delete_thread' requiert `thread_id`")
            _refuse_ignored(op, "op='delete_thread' ne prend que `thread_id`",
                             transcript_id=transcript_id, query=query, filters=filters,
                             response_language=response_language, format_mode=format_mode)
            return _run(lambda: client.delete_askfred_thread(thread_id))
        raise _bad("op inconnu")

    # ================================================================
    # User — profile, directory, groups, role
    # ================================================================

    @mcp.tool()
    def fireflies_user(
        op: Literal["get", "list", "groups", "set_role"] = "get",
        user_id: Optional[str] = None,
        mine: Optional[bool] = None,
        role: Optional[Literal["admin", "user"]] = None,
    ) -> object:
        """A Fireflies user profile, the team directory, user groups, or
        setting a user's admin role.

        Args:
            op: "get" (default, omit `user_id` for the key owner) | "list"
                (whole team) | "groups" | "set_role".
            user_id: op="get" (optional) or "set_role" (required).
            mine: op="groups" only — True scopes to the caller's own groups.
            role: REQUIRED by "set_role" — "admin" or "user".
        """
        client = _client()
        if op == "get":
            _refuse_ignored(op, "op='get' ne prend que `user_id`", mine=mine, role=role)
            return _run(lambda: client.get_user(user_id))
        if op == "list":
            _refuse_ignored(op, "op='list' ne prend aucun paramètre", user_id=user_id, mine=mine, role=role)
            return _run(lambda: client.list_users())
        if op == "groups":
            _refuse_ignored(op, "op='groups' ne prend que `mine`", user_id=user_id, role=role)
            return _run(lambda: client.list_user_groups(mine=mine))
        if op == "set_role":
            if not user_id or not role:
                raise _bad("op='set_role' requiert `user_id` et `role`")
            _refuse_ignored(op, "op='set_role' ne prend que `user_id`/`role`", mine=mine)
            return _run(lambda: client.set_user_role(user_id, role))
        raise _bad("op inconnu")

    # ================================================================
    # Channel
    # ================================================================

    @mcp.tool()
    def fireflies_channel(
        op: Literal["get", "list"] = "list",
        channel_id: Optional[str] = None,
    ) -> object:
        """A channel used to organize meetings — fetch one or list every
        channel visible to the caller (public team channels + private ones
        they belong to).

        Args:
            op: "get" (requires `channel_id`) | "list" (default).
            channel_id: REQUIRED by "get". Refused on "list".
        """
        client = _client()
        if op == "get":
            if not channel_id:
                raise _bad("op='get' requiert `channel_id`")
            return _run(lambda: client.get_channel(channel_id))
        if op == "list":
            _refuse_ignored(op, "op='list' ne prend pas `channel_id`", channel_id=channel_id)
            return _run(lambda: client.list_channels())
        raise _bad("op doit être 'get' ou 'list'")

    # ================================================================
    # Bite (Soundbite)
    # ================================================================

    @mcp.tool()
    def fireflies_bite(
        op: Literal["get", "list", "create"] = "list",
        bite_id: Optional[str] = None,
        mine: Optional[bool] = None,
        transcript_id: Optional[str] = None,
        my_team: Optional[bool] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        name: Optional[str] = None,
        media_type: Optional[Literal["video", "audio"]] = None,
        privacies: Optional[List[Literal["public", "team", "participants"]]] = None,
        summary: Optional[str] = None,
    ) -> object:
        """A Soundbite — a short clip cut from a meeting. Fetch, list, or
        create one.

        Args:
            op: "get" | "list" (default) | "create".
            bite_id: REQUIRED by "get".
            mine/my_team: op="list" only — at least one of `mine`,
                `transcript_id`, `my_team` is required by Fireflies.
            transcript_id: op="list" (filter, see above) or "create"
                (required — source transcript).
            limit: op="list" only, caps at 50.
            skip: op="list" only — pagination offset.
            start_time/end_time: REQUIRED by "create" — clip bounds, seconds.
            name/media_type/privacies/summary: op="create" only.
        """
        client = _client()
        if op == "get":
            if not bite_id:
                raise _bad("op='get' requiert `bite_id`")
            _refuse_ignored(op, "op='get' ne prend que `bite_id`",
                             mine=mine, transcript_id=transcript_id, my_team=my_team, limit=limit,
                             skip=skip, start_time=start_time, end_time=end_time, name=name,
                             media_type=media_type, privacies=privacies, summary=summary)
            return _run(lambda: client.get_bite(bite_id))
        if op == "list":
            _refuse_ignored(op, "op='list' ne prend que mine/transcript_id/my_team/limit/skip",
                             bite_id=bite_id, start_time=start_time, end_time=end_time, name=name,
                             media_type=media_type, privacies=privacies, summary=summary)
            if mine is None and transcript_id is None and my_team is None:
                raise _bad("op='list' requiert au moins un de `mine`, `transcript_id`, `my_team`")
            return _run(lambda: client.list_bites(
                mine=mine, transcript_id=transcript_id, my_team=my_team, limit=limit, skip=skip))
        if op == "create":
            _refuse_ignored(op, "op='create' ne prend pas mine/my_team/limit/skip",
                             bite_id=bite_id, mine=mine, my_team=my_team, limit=limit, skip=skip)
            if not transcript_id or start_time is None or end_time is None:
                raise _bad("op='create' requiert `transcript_id`, `start_time` et `end_time`")
            kwargs = {k: v for k, v in dict(
                name=name, media_type=media_type, privacies=privacies, summary=summary,
            ).items() if v is not None}
            return _run(lambda: client.create_bite(transcript_id, start_time, end_time, **kwargs))
        raise _bad("op inconnu")

    # ================================================================
    # Org — contacts, analytics, AI apps, rule executions, audit log
    # ================================================================

    @mcp.tool()
    def fireflies_org(
        op: Literal["contacts", "analytics", "apps", "rule_executions", "audit_events"] = "contacts",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        app_id: Optional[str] = None,
        transcript_id: Optional[str] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        logs_per_meeting: Optional[int] = None,
        rule_filters: Optional[Dict[str, Any]] = None,
        category: Optional[Literal["MEETING_OPERATIONS", "TEAM_OPERATIONS",
                                    "USER_OPERATIONS", "AUTHENTICATION"]] = None,
        action: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_email: Optional[str] = None,
    ) -> object:
        """Read-only org-wide data with no bigger natural home: contacts (met
        via meetings), team/individual analytics, AI App outputs, automation
        rule execution logs, and the compliance audit log.

        Args:
            op: "contacts" (default) | "analytics" | "apps" |
                "rule_executions" (Enterprise plan) | "audit_events"
                (Enterprise + team-admin, **Beta**).
            start_time/end_time: op="analytics" only — ISO 8601.
            app_id/transcript_id/skip/limit: op="apps" only.
            cursor: op="rule_executions" or "audit_events" — pagination
                cursor from a previous `next_cursor`.
            logs_per_meeting/rule_filters: op="rule_executions" only —
                `rule_filters`: {"rule_id"?, "meeting_id"?, "date_from"?,
                "date_to"?, "is_test"?}. `limit` here caps meeting GROUPS
                (1-50, default 10).
            category: REQUIRED by "audit_events".
            action/date_from/date_to/actor_user_id/actor_email: op=
                "audit_events" only (filters). `limit` here caps events
                (1-50, default 20).
        """
        client = _client()
        if op == "contacts":
            _refuse_ignored(op, "op='contacts' ne prend aucun paramètre",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, limit=limit, cursor=cursor,
                             logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from,
                             date_to=date_to, actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.list_contacts())
        if op == "analytics":
            _refuse_ignored(op, "op='analytics' ne prend que `start_time`/`end_time`",
                             app_id=app_id, transcript_id=transcript_id, skip=skip, limit=limit,
                             cursor=cursor, logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from, date_to=date_to,
                             actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.get_analytics(start_time=start_time, end_time=end_time))
        if op == "apps":
            _refuse_ignored(op, "op='apps' ne prend que app_id/transcript_id/skip/limit",
                             start_time=start_time, end_time=end_time, cursor=cursor,
                             logs_per_meeting=logs_per_meeting, rule_filters=rule_filters,
                             category=category, action=action, date_from=date_from, date_to=date_to,
                             actor_user_id=actor_user_id, actor_email=actor_email)
            return _run(lambda: client.list_apps(
                app_id=app_id, transcript_id=transcript_id, skip=skip, limit=limit))
        if op == "rule_executions":
            _refuse_ignored(op, "op='rule_executions' ne prend que limit/cursor/logs_per_meeting/rule_filters",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, category=category, action=action,
                             date_from=date_from, date_to=date_to, actor_user_id=actor_user_id,
                             actor_email=actor_email)
            return _run(lambda: client.list_rule_executions_by_meeting(
                limit=limit, cursor=cursor, logs_per_meeting=logs_per_meeting, filters=rule_filters))
        if op == "audit_events":
            _refuse_ignored(op, "op='audit_events' ne prend pas ces paramètres",
                             start_time=start_time, end_time=end_time, app_id=app_id,
                             transcript_id=transcript_id, skip=skip, logs_per_meeting=logs_per_meeting,
                             rule_filters=rule_filters)
            if not category:
                raise _bad("op='audit_events' requiert `category`")
            return _run(lambda: client.list_audit_events(
                category, limit=limit, cursor=cursor, action=action, date_from=date_from,
                date_to=date_to, actor_user_id=actor_user_id, actor_email=actor_email))
        raise _bad("op inconnu")
