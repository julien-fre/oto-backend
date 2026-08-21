"""Grain — meeting recordings, transcripts, sharing, webhooks, workspace org data.

Wrappe `oto.tools.grain.client.GrainClient` (API v2, Bearer +
`Public-Api-Version`). keyed `api_key`, byo-only (pas de clé plateforme) :
chaque user/org pose SA clé Grain — Personal Access Token (par user) ou
Workspace Access Token (admin, « accès à TOUTES les données du workspace »),
toutes deux un Bearer simple ici, Grain applique le scope lui-même.

**5 tools, un par objet métier** (silae, ADR 0047) — Grain a plus d'objets
distincts que Granola, donc pas de fusion à 2 comme pour ce dernier :
- `grain_recording` — CRUD + partage d'une réunion (op=list/get/update/tag/
  untag/share_user/unshare_user/share_team/unshare_team).
- `grain_transcript` — les 4 formats de transcript (json/txt/vtt/srt), à part
  parce que la nature de la réponse (texte brut vs JSON) diffère radicalement
  du reste des tools ici.
- `grain_recording_file` — download + obtention d'une URL d'upload, à part
  parce que ce sont des octets bruts, pas du JSON (op=download/create_upload_url).
- `grain_hook` — CRUD complet des webhooks (op=list/create/delete) — pas
  destructeur de DONNÉE utilisateur, juste de la plomberie d'intégration
  (même doctrine que `granola_webhook_endpoint`).
- `grain_org` — les 3 listes sans filtre (users/teams/meeting_types),
  fusionnées en un seul tool tant elles sont triviales (op=users/teams/
  meeting_types).

**Aucun param n'est retenu au silence** : un `op` qui ne reconnaît pas un
argument fourni REFUSE plutôt que de l'ignorer (silae `_refuse_ignored`).

⚠️ **Aucun spec machine-readable n'existe pour cette API** (openapi.json/
docs.json/mint.json/llms.txt rendent tous 403, un blocage WAF constant, pas
un 404 d'absence) — tout ici vient d'abord d'une lecture de pages de doc.

**Testé en live le 2026-08-20** avec un Personal Access Token réel
(workspace folk.app) : 20 des 21 méthodes ont marché du premier coup —
list/get recordings, les 4 formats de transcript, tag/untag, share/unshare
user, update (renommage), download (21 Mo réels), create_upload_url (une
vraie URL S3 pré-signée — confirme le choix de ne PAS y envoyer le Bearer
Grain), et le cycle complet des hooks (create contre une URL joignable
réelle, list, delete). **Un vrai bug trouvé et corrigé** : `share_with_team`
attendait `team_id` dans le corps JSON (PUT pluriel `.../teams`), pas dans
le chemin comme la doc le suggérait — voir `GrainClient.share_with_team`
pour le détail. Exactement la classe de bug
que la vérification OpenAPI d'Ahrefs avait attrapée et qu'une recherche
doc-seule avait manquée ici aussi.

⚠️ **Un Personal Access Token N'EST PAS scopé aux réunions du porteur.**
Vérifié en live : `grain_recording(op="list")` sans filtre rend les
enregistrements `share_state="public"` de TOUTE l'organisation (vus dans le
test : des réunions enregistrées par d'autres membres du workspace, où le
porteur du token n'était même pas participant) — le PAT donne accès aux
notes personnelles (possédées/partagées) **ET** aux notes publiques de
l'espace, pas seulement aux siennes. Pour scoper à « mes réunions » :
`filter={"attendance": "hosted"}` (animées par le porteur) ou `"attended"`
(auxquelles il a participé) — confirmé en live, ces deux valeurs filtrent
bien. Sans ce filtre, un agent qui liste sans précaution peut remonter des
appels clients d'un collègue.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention,
    pas un détail — sinon `grain_recording(op="list", recording_id=...)`
    rendrait TOUTES les réunions en laissant croire que `recording_id` a
    filtré sur une seule."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Grain a rejeté la clé API (HTTP {status}) — vérifie la clé posée sur ce "
                "connecteur (Grain : Settings → Integrations → API).")
    if status == 404:
        return f"Grain : ressource introuvable (HTTP 404) — {e.body}"
    if status == 429:
        return ("Grain : trop de requêtes (429) — limite 300/minute. "
                "Réessaie dans un instant.")
    if status in (500, 502, 503, 504):
        return f"Grain est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Grain a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : liste des types de réunion, sans
    filtre, la plus légère des 3 listes org."""
    from oto.tools.grain.client import GrainClient
    GrainClient(api_key=fields["key"]).list_meeting_types()


def register(mcp: FastMCP) -> None:
    from oto.tools.grain.client import GrainClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("grain", _verify)

    def _client() -> GrainClient:
        key, _ = access.resolve_api_key("grain")
        return GrainClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Recordings — CRUD + sharing
    # ================================================================

    @mcp.tool()
    def grain_recording(
        op: Literal["list", "get", "update", "tag", "untag",
                     "share_user", "unshare_user", "share_team", "unshare_team"] = "list",
        recording_id: Optional[str] = None,
        cursor: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        include: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        tag: Optional[str] = None,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> object:
        """A meeting recording — list, fetch, rename, tag, or share one.

        ⚠️ op="list" WITHOUT `filter.attendance` returns every workspace-public
        recording, not just the caller's — confirmed live (2026-08-20): a
        Personal Access Token sees its owner's own/shared notes AND every
        `share_state="public"` recording made by anyone else in the org, even
        ones the token owner never attended. Pass `filter={"attendance":
        "hosted"}` (recordings the caller ran) or `"attended"` (recordings
        they were in) to scope to "my meetings only" — omit it and an agent
        can surface a colleague's client call.

        Args:
            op: "list" (default) | "get" | "update" | "tag" | "untag" |
                "share_user" | "unshare_user" | "share_team" | "unshare_team".
            recording_id: REQUIRED by every op except "list". Refused on "list".
            cursor: op="list" only — pagination cursor from a previous response.
            filter: op="list" only — {"before_datetime"?, "after_datetime"?,
                "attendance"? ("hosted"|"attended", Personal-key only — see
                the warning above),
                "participant_scope"? ("internal"|"external"), "title_search"?,
                "team"? (uuid), "meeting_type"? (uuid)}.
            include: "list"/"get" — {"highlights"?, "participants"?,
                "ai_action_items"?, "ai_summary"?, "private_notes"?
                (Personal-key only), "calendar_event"?, "hubspot"?,
                "screenshares"?, "ai_template_sections"?: {"format": "json"|
                "markdown"|"text"}}. Highlights are NOT a separate tool — set
                `include={"highlights": True}` to get them here. Confirmed
                live: `include={"hubspot": True}` returns
                `{"hubspot_company_ids": [...], "hubspot_deal_ids": [...]}`;
                `include={"participants": True}` items carry `hs_contact_id`
                (null if unlinked).
            title: REQUIRED by "update" — the new title.
            tag: REQUIRED by "tag"/"untag".
            user_id: REQUIRED by "share_user"/"unshare_user".
            team_id: REQUIRED by "share_team"/"unshare_team".
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='get' pour une réunion précise",
                             recording_id=recording_id, title=title, tag=tag,
                             user_id=user_id, team_id=team_id)
            body = {k: v for k, v in dict(cursor=cursor, filter=filter, include=include).items()
                    if v is not None}
            return _run(lambda: client.list_recordings(**body))
        if recording_id is None:
            raise _bad(f"op={op!r} requiert `recording_id`")
        if op == "get":
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list'",
                             cursor=cursor, filter=filter, title=title, tag=tag,
                             user_id=user_id, team_id=team_id)
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.get_recording(recording_id, **kwargs))
        if op == "update":
            _refuse_ignored(op, "op='update' ne change que le titre",
                             cursor=cursor, filter=filter, include=include, tag=tag,
                             user_id=user_id, team_id=team_id)
            if not title:
                raise _bad("op='update' requiert `title`")
            return _run(lambda: client.update_recording(recording_id, title))
        if op in ("tag", "untag"):
            _refuse_ignored(op, f"op={op!r} ne prend que `tag`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             user_id=user_id, team_id=team_id)
            if not tag:
                raise _bad(f"op={op!r} requiert `tag`")
            if op == "tag":
                return _run(lambda: client.add_tag(recording_id, tag))
            return _run(lambda: client.remove_tag(recording_id, tag))
        if op in ("share_user", "unshare_user"):
            _refuse_ignored(op, f"op={op!r} ne prend que `user_id`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             tag=tag, team_id=team_id)
            if not user_id:
                raise _bad(f"op={op!r} requiert `user_id`")
            if op == "share_user":
                return _run(lambda: client.share_with_user(recording_id, user_id))
            return _run(lambda: client.unshare_from_user(recording_id, user_id))
        if op in ("share_team", "unshare_team"):
            _refuse_ignored(op, f"op={op!r} ne prend que `team_id`",
                             cursor=cursor, filter=filter, include=include, title=title,
                             tag=tag, user_id=user_id)
            if not team_id:
                raise _bad(f"op={op!r} requiert `team_id`")
            if op == "share_team":
                return _run(lambda: client.share_with_team(recording_id, team_id))
            return _run(lambda: client.unshare_from_team(recording_id, team_id))
        raise _bad("op inconnu")

    # ================================================================
    # Transcript — 4 formats
    # ================================================================

    @mcp.tool()
    def grain_transcript(
        recording_id: str,
        format: Literal["json", "txt", "vtt", "srt"] = "json",  # noqa: A002
    ) -> object:
        """A recording's transcript, in the requested format.

        Args:
            recording_id: the recording id.
            format: "json" (default, structured) | "txt" (plain text) |
                "vtt" (WebVTT, for video players) | "srt" (SubRip, for video editors).
        """
        client = _client()
        if format == "json":
            return _run(lambda: client.get_transcript(recording_id))
        if format == "txt":
            return _run(lambda: client.get_transcript_text(recording_id))
        if format == "vtt":
            return _run(lambda: client.get_transcript_vtt(recording_id))
        if format == "srt":
            return _run(lambda: client.get_transcript_srt(recording_id))
        raise _bad("format doit être 'json', 'txt', 'vtt' ou 'srt'")

    # ================================================================
    # Recording file — upload/download (raw bytes)
    # ================================================================

    @mcp.tool()
    def grain_recording_file(
        op: Literal["download", "create_upload_url"] = "download",
        recording_id: Optional[str] = None,
        filename: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> object:
        """A recording's underlying file — download the raw bytes, or get a
        one-time URL to upload a new recording.

        Note: uploading the actual file bytes to the URL this returns is NOT
        done through MCP (binary payload, no natural fit for a JSON tool
        call) — `op="create_upload_url"` gives you the `url` to PUT the file
        to directly; `oto.tools.grain.client.GrainClient.upload_recording_file`
        is available for scripted use outside the agent loop.

        Args:
            op: "download" (default) | "create_upload_url".
            recording_id: REQUIRED by "download". Refused on "create_upload_url".
            filename: REQUIRED by "create_upload_url" — the file being uploaded
                (.mov/.mp4/.mp3/.m4a). Refused on "download".
            user_id: op="create_upload_url" with a Workspace API key only —
                who owns the resulting recording. Refused on "download".
        """
        client = _client()
        if op == "download":
            _refuse_ignored(op, "utilise op='create_upload_url' pour en envoyer une nouvelle",
                             filename=filename, user_id=user_id)
            if not recording_id:
                raise _bad("op='download' requiert `recording_id`")
            content = _run(lambda: client.download_recording(recording_id))
            return {"recording_id": recording_id, "size_bytes": len(content),
                    "note": "Binary content returned as bytes, not shown inline."}
        if op == "create_upload_url":
            _refuse_ignored(op, "op='create_upload_url' ne télécharge rien",
                             recording_id=recording_id)
            if not filename:
                raise _bad("op='create_upload_url' requiert `filename`")
            kwargs = {"user_id": user_id} if user_id is not None else {}
            return _run(lambda: client.create_upload_url(filename, **kwargs))
        raise _bad("op doit être 'download' ou 'create_upload_url'")

    # ================================================================
    # Hooks (webhooks) — full CRUD
    # ================================================================

    @mcp.tool()
    def grain_hook(
        op: Literal["list", "create", "delete"] = "list",
        hook_id: Optional[str] = None,
        hook_url: Optional[str] = None,
        hook_type: Optional[Literal[
            "recording_added", "recording_updated", "recording_deleted",
            "highlight_added", "highlight_updated", "highlight_deleted",
            "story_added", "story_updated", "story_deleted", "upload_status",
        ]] = None,
        include: Optional[Dict[str, Any]] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> object:
        """A webhook subscription — list, create, or delete one. This is the
        ENTIRE hook surface: Grain has no update/PATCH endpoint for hooks,
        confirmed against the doc page — "list"/"create"/"delete" is
        everything there is, not a partial cover. Unlike most write-capable
        tools here, the full CRUD is exposed: a webhook subscription isn't
        destructive of user data, just integration plumbing.

        Args:
            op: "list" (default) | "create" | "delete".
            hook_id: REQUIRED by "delete". Refused on "list"/"create".
            hook_url: REQUIRED by "create" — an HTTPS endpoint. Grain tests
                reachability at creation time: it must answer 2xx immediately
                or the call fails (confirmed live 2026-08-20 against a real URL).
            hook_type: REQUIRED by "create". "story_*" types are the ONLY way
                to observe Stories — there is no REST endpoint to list/get
                them directly (confirmed); the webhook payload IS the data.
            include: op="create" only — for recording_added/recording_updated,
                same shape as `grain_recording`'s `include`; for
                highlight_added/highlight_updated, `{"transcript": bool,
                "speakers": bool}`; other hook_types take {} (omit).
            filter: op="list" only — {"hook_type"?, "state"?: "enabled"|"disabled"}.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='create' pour en enregistrer un nouveau, "
                             "op='delete' pour en cibler un existant",
                             hook_id=hook_id, hook_url=hook_url, hook_type=hook_type,
                             include=include)
            body = {"filter": filter} if filter is not None else {}
            return _run(lambda: client.list_hooks(**body))
        if op == "create":
            _refuse_ignored(op, "un nouveau hook n'a pas encore d'id, `filter` ne vaut que pour 'list'",
                             hook_id=hook_id, filter=filter)
            if not hook_url or not hook_type:
                raise _bad("op='create' requiert `hook_url` et `hook_type`")
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.create_hook(hook_url, hook_type, **kwargs))
        if op == "delete":
            _refuse_ignored(op, "une suppression ne prend que `hook_id`",
                             hook_url=hook_url, hook_type=hook_type, include=include, filter=filter)
            if not hook_id:
                raise _bad("op='delete' requiert `hook_id`")
            return _run(lambda: client.delete_hook(hook_id))
        raise _bad("op doit être 'list', 'create' ou 'delete'")

    # ================================================================
    # Org — users, teams, meeting types (all list-only, no filters)
    # ================================================================

    @mcp.tool()
    def grain_org(op: Literal["users", "teams", "meeting_types"] = "users") -> object:
        """Workspace org data — users, teams, or meeting types. No filters:
        each call returns everything the key can see.

        Args:
            op: "users" (default, {id, name, email}) | "teams" ({id, name}) |
                "meeting_types" ({id, name, scope}) — `team`/`meeting_type`
                ids from these feed `grain_recording`'s `filter`.
        """
        client = _client()
        if op == "users":
            return _run(lambda: client.list_users())
        if op == "teams":
            return _run(lambda: client.list_teams())
        if op == "meeting_types":
            return _run(lambda: client.list_meeting_types())
        raise _bad("op doit être 'users', 'teams' ou 'meeting_types'")
