"""Granola — meeting notes, transcripts, folders, webhook endpoints.

Wrappe `oto.tools.granola.client.GranolaClient` (API v1, Bearer). keyed
`api_key`, byo-only (pas de clé plateforme) : chaque user/org pose SA clé
Granola — deux natures de clé côté Granola (personnelle, self-serve ; workspace,
provisionnée par un admin), toutes deux un simple Bearer ici, Granola applique
le scope lui-même.

Petite API (8 opérations, 4 objets), groupée en **DEUX tools par catégorie** :
- `granola_content` — tout ce qui LIT le contenu de réunion (notes, transcript,
  dossiers), verbe en `op`.
- `granola_webhook_endpoint` — la gestion d'intégration (webhook endpoints),
  verbe en `op`, CRUD complet.

**Aucun param n'est retenu au silence** : un `op` qui ne reconnaît pas un
argument fourni REFUSE plutôt que de l'ignorer (silae `_refuse_ignored`) —
`granola_content(op="list_notes", note_id=...)` rendrait TOUTES les notes en
laissant croire que `note_id` a filtré sur une seule.

**Webhook endpoints : CRUD complet exposé**, contrairement à la doctrine
Management d'Ahrefs (lecture+création seulement) — supprimer/désactiver un
webhook endpoint n'est pas destructeur de DONNÉE utilisateur (notes,
transcripts…), c'est de la plomberie d'intégration, faible rayon d'effet.

**Vérifié contre le spec OpenAPI 3.1.0 réel** (`docs.granola.ai/api-reference/
openapi.json`, 2026-08-20), pas contre un résumé de page doc — required,
formes de corps, bornes `page_size`. **Testé en live le 2026-08-20** avec une
clé workspace réelle (Julien) : notes, transcript, dossiers, et le cycle
complet création→modification→suppression d'un webhook endpoint répondent
exactement comme codé, y compris les erreurs 400 de validation (`page_size`
hors bornes, `note_id` invalide, `scopes` incompatible avec une clé workspace
— confirmé : une clé workspace DOIT passer `scopes=["workspace"]`, exactement
comme documenté). Le 9e endpoint du spec, `GET /v1/audit`, a rendu
`404 NOT_FOUND` sur cette clé (probablement gaté par plan) — retiré plutôt que
d'exposer un appel que personne ne peut actuellement utiliser (cf. oto-core
`GranolaClient`).
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention,
    pas un détail — sinon `granola_content(op="get_note", folder_id=...)`
    rendrait UNE note en laissant croire que `folder_id` a filtré quelque chose."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Granola a rejeté la clé API (HTTP {status}) — vérifie la clé posée sur ce "
                "connecteur (Granola : Settings → Connectors → API keys).")
    if status == 404:
        return f"Granola : ressource introuvable (HTTP 404) — {e.body}"
    if status == 429:
        return ("Granola : trop de requêtes (429) — limite 25 req/5s en rafale, "
                "5 req/s (300/min) soutenu. Réessaie dans un instant.")
    if status in (500, 502, 503, 504):
        return f"Granola est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Granola a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : liste des webhook endpoints, sans param,
    gratuite (aucune limite d'unités documentée côté Granola)."""
    from oto.tools.granola.client import GranolaClient
    GranolaClient(api_key=fields["key"]).list_webhook_endpoints()


def register(mcp: FastMCP) -> None:
    from oto.tools.granola.client import GranolaClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("granola", _verify)

    def _client() -> GranolaClient:
        key, _ = access.resolve_api_key("granola")
        return GranolaClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Content — notes, transcripts, folders (read-only)
    # ================================================================

    @mcp.tool()
    def granola_content(
        op: Literal["list_notes", "get_note", "get_transcript", "list_folders"] = "list_notes",
        note_id: Optional[str] = None,
        include: Optional[Literal["transcript"]] = None,
        created_before: Optional[str] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        folder_id: Optional[str] = None,
        cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> object:
        """Meeting content — notes, transcripts, and folders.

        Args:
            op: "list_notes" (default) | "get_note" | "get_transcript" | "list_folders".
            note_id: REQUIRED by "get_note"/"get_transcript" — the note id
                (`not_...`). Refused on "list_notes"/"list_folders", which
                enumerate everything reachable and would ignore it.
            include: op="get_note" only — "transcript" inlines the transcript
                in the response; may return `TRANSCRIPT_TOO_LARGE` for long
                meetings, use op="get_transcript" then (paginated from the start).
            created_before/created_after/updated_after: op="list_notes"
                filters — date (YYYY-MM-DD) or date-time (ISO 8601).
            folder_id: op="list_notes" — restrict to this folder and its
                subfolders (from op="list_folders").
            cursor: pagination cursor from a previous response — valid on
                "list_notes"/"get_transcript"/"list_folders".
            page_size: "list_notes"/"list_folders" — 1-30, default 10.
                "get_transcript" — 1-100, default 50.
        """
        client = _client()
        if op == "list_notes":
            _refuse_ignored(op, "utilise op='get_note' pour une note précise",
                             note_id=note_id, include=include)
            params = {k: v for k, v in dict(
                created_before=created_before, created_after=created_after,
                updated_after=updated_after, folder_id=folder_id, cursor=cursor,
                page_size=page_size).items() if v is not None}
            return _run(lambda: client.list_notes(**params))
        if op == "get_note":
            if not note_id:
                raise _bad("op='get_note' requiert `note_id`")
            _refuse_ignored(op, "ces filtres ne s'appliquent qu'à op='list_notes'/'list_folders'",
                             created_before=created_before, created_after=created_after,
                             updated_after=updated_after, folder_id=folder_id,
                             cursor=cursor, page_size=page_size)
            kwargs = {"include": include} if include is not None else {}
            return _run(lambda: client.get_note(note_id, **kwargs))
        if op == "get_transcript":
            if not note_id:
                raise _bad("op='get_transcript' requiert `note_id`")
            _refuse_ignored(op, "ces filtres ne s'appliquent pas à op='get_transcript'",
                             include=include, created_before=created_before,
                             created_after=created_after, updated_after=updated_after,
                             folder_id=folder_id)
            kwargs = {k: v for k, v in dict(cursor=cursor, page_size=page_size).items()
                      if v is not None}
            return _run(lambda: client.get_transcript(note_id, **kwargs))
        if op == "list_folders":
            _refuse_ignored(op, "utilise op='list_notes' pour filtrer des notes",
                             note_id=note_id, include=include, created_before=created_before,
                             created_after=created_after, updated_after=updated_after,
                             folder_id=folder_id)
            kwargs = {k: v for k, v in dict(cursor=cursor, page_size=page_size).items()
                      if v is not None}
            return _run(lambda: client.list_folders(**kwargs))
        raise _bad("op doit être 'list_notes', 'get_note', 'get_transcript' ou 'list_folders'")

    # ================================================================
    # Webhook endpoints — integration management (CRUD)
    # ================================================================

    @mcp.tool()
    def granola_webhook_endpoint(
        op: Literal["list", "create", "update", "delete"] = "list",
        webhook_endpoint_id: Optional[str] = None,
        url: Optional[str] = None,
        scopes: Optional[List[Literal["personal", "public", "workspace"]]] = None,
        events: Optional[List[Literal["note.access_granted", "note.edited", "note.generated"]]] = None,
        folder_ids: Optional[List[str]] = None,
        enabled: Optional[bool] = None,
    ) -> object:
        """A webhook endpoint receiving Granola event deliveries — list,
        create, update, or delete one. Unlike most write-capable tools here,
        the full CRUD lifecycle is exposed: managing a webhook subscription
        isn't destructive of user data (notes/transcripts), just integration
        plumbing.

        Args:
            op: "list" (default) | "create" | "update" | "delete".
            webhook_endpoint_id: REQUIRED by "update"/"delete" (`whe_...`).
                Refused on "list"/"create".
            url: REQUIRED by "create" — publicly reachable HTTPS URL to
                deliver events to. Optional on "update" (change it).
            scopes: REQUIRED by "create" — which notes to receive events for:
                `["personal"]` (notes you own/shared with you), `["public"]`
                (workspace-visible notes), or both. A Workspace API key must
                pass exactly `["workspace"]` (confirmed live). Optional on "update".
            events: which event types to subscribe to (omit on "create" = all
                three). Optional on "update".
            folder_ids: restrict delivery to these folders + their subfolders
                (from `granola_content(op="list_folders")`, max 100). Omit =
                every note matching `scopes`. Optional on "update".
            enabled: "update" only — pause/resume delivery without deleting
                the endpoint.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='create' pour en enregistrer un nouveau, "
                             "op='update'/'delete' pour en cibler un existant",
                             webhook_endpoint_id=webhook_endpoint_id, url=url, scopes=scopes,
                             events=events, folder_ids=folder_ids, enabled=enabled)
            return _run(lambda: client.list_webhook_endpoints())
        if op == "create":
            _refuse_ignored(op, "un nouvel endpoint n'a pas encore d'id, ni d'état enabled",
                             webhook_endpoint_id=webhook_endpoint_id, enabled=enabled)
            if not url or not scopes:
                raise _bad("op='create' requiert `url` et `scopes`")
            body = {k: v for k, v in dict(events=events, folder_ids=folder_ids).items()
                    if v is not None}
            return _run(lambda: client.create_webhook_endpoint(url, scopes, **body))
        if op in ("update", "delete"):
            if not webhook_endpoint_id:
                raise _bad(f"op={op!r} requiert `webhook_endpoint_id`")
            if op == "delete":
                _refuse_ignored(op, "une suppression ne prend pas d'autre champ",
                                 url=url, scopes=scopes, events=events,
                                 folder_ids=folder_ids, enabled=enabled)
                return _run(lambda: client.delete_webhook_endpoint(webhook_endpoint_id))
            body = {k: v for k, v in dict(url=url, scopes=scopes, events=events,
                                           folder_ids=folder_ids, enabled=enabled).items()
                    if v is not None}
            if not body:
                raise _bad("op='update' requiert au moins un champ à modifier")
            return _run(lambda: client.update_webhook_endpoint(webhook_endpoint_id, **body))
        raise _bad("op doit être 'list', 'create', 'update' ou 'delete'")
